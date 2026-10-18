"""LangGraph execution and SSE transport using public Python/LangGraph APIs."""
import asyncio
import json
from runtime import new_context, request_context

def sse(data):
    return "data: " + json.dumps(data, ensure_ascii=False, default=str) + "\n\n"

class GraphService:
    """Share one graph/checkpointer and track cancellable requests by run id."""
    def __init__(self):
        self._graph = None
        self.running_tasks = {}

    def _get_graph(self, ctx=None):
        if self._graph is None:
            from agents.agent import build_agent
            self._graph = build_agent(ctx)
        return self._graph

    def _input(self, payload):
        messages = payload.get("messages")
        if messages is None:
            text = payload.get("text") or payload.get("message", "")
            messages = [{"role": "user", "content": text}]
        return {"messages": messages}

    def _config(self, payload, ctx):
        # Stable session id preserves history; independent run id enables cancellation.
        session = payload.get("session_id") or ctx.session_id or ctx.run_id
        ctx.session_id = session
        return {"configurable": {"thread_id": session}, "recursion_limit": 100}

    async def run(self, payload, ctx=None):
        ctx = ctx or new_context("run")
        token = request_context.set(ctx)
        try:
            return await self._get_graph(ctx).ainvoke(self._input(payload), config=self._config(payload, ctx))
        finally:
            request_context.reset(token)

    async def deltas(self, payload, ctx):
        """Forward text tokens and tool results, including events from nested agents."""
        token = request_context.set(ctx)
        try:
            graph = self._get_graph(ctx)
            streamed_runs = set()
            async for event in graph.astream_events(self._input(payload), config=self._config(payload, ctx), version="v2"):
                data = event.get("data", {})
                if event["event"] == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    content = getattr(chunk, "content", "")
                    if isinstance(content, list):
                        content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
                    if content:
                        streamed_runs.add(event["run_id"])
                        yield {"content": content}
                elif event["event"] == "on_chat_model_end" and event["run_id"] not in streamed_runs:
                    # Providers without token streaming still emit a completed message.
                    output = data.get("output")
                    content = getattr(output, "content", "")
                    if isinstance(content, list):
                        content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
                    if content:
                        yield {"content": content}
                elif event["event"] == "on_tool_end":
                    output = data.get("output", "")
                    content = getattr(output, "content", output)
                    if not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False, default=str)
                    yield {"role": "tool", "content": content, "name": event.get("name", "")}
        finally:
            request_context.reset(token)

    async def stream_sse(self, payload, ctx, *, openai=False):
        """Queue events for heartbeats; cancel producer on disconnect or timeout."""
        queue = asyncio.Queue(maxsize=100)
        marker = object()
        async def produce():
            try:
                async with asyncio.timeout(900):
                    async for delta in self.deltas(payload, ctx):
                        await queue.put(delta)
            except Exception as exc:
                await queue.put({"error": {"message": str(exc), "type": type(exc).__name__}})
            finally:
                if asyncio.current_task().cancelling():
                    try:
                        queue.put_nowait(marker)
                    except asyncio.QueueFull:
                        pass
                else:
                    await queue.put(marker)
        task = asyncio.create_task(produce())
        self.running_tasks[ctx.run_id] = task
        try:
            while True:
                try:
                    delta = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    yield ": heartbeat\n\n"
                    continue
                if delta is marker:
                    break
                if "error" in delta:
                    yield sse(delta)
                elif openai:
                    yield sse({"id": "chatcmpl-" + ctx.run_id, "object": "chat.completion.chunk",
                               "model": "agent", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                else:
                    yield sse(delta)
            if openai:
                yield sse({"id": "chatcmpl-" + ctx.run_id, "object": "chat.completion.chunk", "model": "agent",
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]\n\n"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.running_tasks.pop(ctx.run_id, None)

    def cancel_run(self, run_id, ctx=None):
        task = self.running_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            return {"status": "success", "run_id": run_id}
        return {"status": "not_found", "run_id": run_id}

    def graph_inout_schema(self):
        graph = self._get_graph()
        return {"input_schema": graph.get_input_schema().model_json_schema(),
                "output_schema": graph.get_output_schema().model_json_schema()}

    async def run_node(self, node_id, payload, ctx=None):
        graph = self._get_graph(ctx)
        node = graph.builder.nodes.get(node_id)
        if node is None:
            raise KeyError(node_id)
        ctx = ctx or new_context("node_run")
        return await node.runnable.ainvoke(self._input(payload), config=self._config(payload, ctx))
