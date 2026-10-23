// ── Init ──
window.addEventListener('load', () => {
  const isNewSession = !localStorage.getItem('doc_session_id_visited');
  if (isNewSession) {
    localStorage.setItem('doc_session_id_visited', '1');
    appendMessage('assistant',
      '您好，请选择操作方式：\n\n' +
      '· 点击下方「内置模板」选择预设模板\n' +
      '· 点击「上传模板」上传Word文档自动识别\n' +
      '· 点击「知识文件上传」批量上传文件提取信息\n' +
      '· 或直接输入消息开始'
    );
  } else {
    appendMessage('assistant', '已恢复上次对话，您可以继续操作。');
  }
  updateStepBar();
});
