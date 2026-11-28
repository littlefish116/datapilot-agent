/* DataPilot V3.4 local-only web client. No third-party scripts, no eval or innerHTML. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const messages = $('messages');
  const input = $('question');
  const form = $('chat-form');
  const sendButton = $('send');
  const sessionState = $('session-state');
  const errorBanner = $('error-banner');
  const newChatButtons = [$('new-chat-side'), $('new-chat-top')];
  const examples = Array.from(document.querySelectorAll('.example'));
  const SVG = 'http://www.w3.org/2000/svg';
  const statuses = {
    ok: '已完成', partial: '部分结果', clarify: '需要澄清',
    unsupported: '不支持', needs_review: '需要人工复核', error: '执行失败',
  };
  const toolNames = {
    sql_analysis: '动态 SQL', business_report: '固定报表',
    delivery_risk_prediction: '延期风险评分',
  };
  const columnNames = {
    purchase_month: '购买月份', order_date: '订单日期', order_count: '订单数',
    delivered_with_dates: '有效日期已交付订单', late_orders: '延期订单',
    late_rate_pct: '延期率 (%)', n: '数量',
  };
  let conversationId = null;
  let busy = false;

  function node(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }
  function svg(tag, attributes, value) {
    const el = document.createElementNS(SVG, tag);
    for (const [key, val] of Object.entries(attributes || {})) el.setAttribute(key, String(val));
    if (value !== undefined) el.textContent = String(value);
    return el;
  }
  function fmt(value) {
    if (value === null || value === undefined) return 'NULL';
    if (typeof value === 'number' && Number.isFinite(value)) return value.toLocaleString('zh-CN', {maximumFractionDigits: 6});
    if (typeof value === 'string' || typeof value === 'boolean') return String(value);
    return '[复杂数据]';
  }
  function scrollEnd() { messages.scrollTop = messages.scrollHeight; }
  function setBusy(value) {
    busy = value;
    sendButton.disabled = value;
    input.disabled = value;
    newChatButtons.forEach((b) => { b.disabled = value; });
    examples.forEach((b) => { b.disabled = value; });
    $('composer-note').textContent = value ? '模型正在处理，请勿重复提交…' : 'Enter 发送 · Shift + Enter 换行 · 每次提问可能产生 API 费用';
  }
  function showError(text) { errorBanner.textContent = text; errorBanner.hidden = false; }
  function clearError() { errorBanner.hidden = true; errorBanner.textContent = ''; }
  function newConversation() {
    if (busy) return;
    conversationId = null;
    sessionState.textContent = '尚未建立会话';
    messages.replaceChildren();
    const welcome = node('section', 'welcome');
    const mark = node('div', 'welcome-mark', '✦');
    const eyebrow = node('div', 'eyebrow', 'NEW CONVERSATION');
    const heading = node('h2', '', '新会话已建立');
    const hint = node('p', '', '上下文已清空。请输入完整问题开始新的分析。');
    welcome.append(mark, eyebrow, heading, hint);
    messages.appendChild(welcome);
    clearError();
    input.value = '';
    input.focus();
  }
  function addTurn(question) {
    const welcome = $('welcome') || messages.querySelector('.welcome');
    if (welcome) welcome.remove();
    const turn = node('section', 'turn');
    const questionNode = node('div', 'user-bubble', question);
    questionNode.setAttribute('aria-label', '你的提问');
    const assistant = node('article', 'answer-card loading');
    const heading = node('div', 'response-heading');
    heading.append(node('span', 'answer-avatar', 'D'), node('strong', '', 'DataPilot Agent'));
    assistant.append(heading, node('p', 'answer-text', '正在选择工具并查询数据…'));
    turn.append(questionNode, assistant);
    messages.appendChild(turn);
    scrollEnd();
    return assistant;
  }
  function badge(text, status) {
    const cls = status === 'ok' ? 'ok' : status === 'partial' || status === 'needs_review' || status === 'clarify' || status === 'unsupported' ? 'warn' : 'error';
    return node('span', 'response-tag ' + cls, text);
  }
  function displayAudit(container, data) {
    const result = data.tool_result && typeof data.tool_result === 'object' ? data.tool_result : null;
    const ev = result && result.evidence && typeof result.evidence === 'object' ? result.evidence : {};
    if (!result && !data.route_usage) return;
    const details = node('details', 'audit');
    details.appendChild(node('summary', '', '查看工具执行与审计信息'));
    const dl = node('dl');
    const pair = (a, b) => {
      if (b === null || b === undefined || b === '') return;
      dl.append(node('dt', '', a), node('dd', '', typeof b === 'object' ? JSON.stringify(b) : String(b)));
    };
    pair('工具', result ? result.tool : data.selected_tool);
    pair('执行状态', result ? result.status : data.status);
    pair('数据来源', ev.source);
    pair('报表', ev.report_id);
    pair('SQL 耗时', typeof ev.execution_ms === 'number' ? ev.execution_ms + ' ms' : null);
    pair('路由 Tokens', data.route_usage && data.route_usage.total_tokens);
    pair('SQL Tokens', ev.usage && ev.usage.total_tokens);
    pair('自动修复成功', data.automatic_repair === true ? '是（仍需业务语义复核）' : '否');
    details.appendChild(dl);
    const preSection = (title, value) => {
      if (!value) return;
      details.appendChild(node('h4', '', title));
      details.appendChild(node('pre', '', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
    };
    preSection('原始生成 SQL', ev.generated_sql);
    preSection('实际执行 SQL', ev.executed_sql);
    preSection('语义风险检查', ev.risk_review);
    preSection('SQL 修复审计', ev.recovery);
    container.appendChild(details);
  }
  function displayPrediction(container, result) {
    if (result.tool !== 'delivery_risk_prediction' || result.status !== 'ok') return;
    const data = result.data;
    if (!data || typeof data !== 'object' || !Number.isFinite(data.risk_score)) return;
    const metric = node('section', 'metric-card');
    metric.append(node('div', 'section-label', '订单延期风险模型评分（非概率）'));
    metric.append(node('strong', '', data.risk_score.toFixed(6)));
    metric.append(node('p', '', data.notice || '未经概率校准，不能解释为真实延期概率或自动划分风险等级。'));
    container.appendChild(metric);
  }
  function columnsOf(data) {
    const rows = data.rows;
    if (!Array.isArray(rows) || !rows.length || !rows[0] || typeof rows[0] !== 'object' || Array.isArray(rows[0])) return [];
    const source = Array.isArray(data.columns) && data.columns.length ? data.columns : Object.keys(rows[0]);
    return source.filter((c) => typeof c === 'string' && c.length < 120).slice(0, 24);
  }
  function displayTable(container, data) {
    const rows = data.rows;
    if (!Array.isArray(rows) || !rows.length) return;
    const columns = columnsOf(data);
    if (!columns.length) return;
    const section = node('section', 'result-block');
    section.appendChild(node('h3', 'block-title', '查询数据明细'));
    const wrap = node('div', 'table-scroll');
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('tabindex', '0');
    wrap.setAttribute('aria-label', '原始查询结果数据表');
    const table = node('table', 'data-table');
    const head = node('thead');
    const hr = node('tr');
    columns.forEach((c) => {
      const th = node('th', '', columnNames[c] || c);
      th.scope = 'col';
      hr.appendChild(th);
    });
    head.appendChild(hr);
    table.appendChild(head);
    const body = node('tbody');
    const maxRows = 100;
    rows.slice(0, maxRows).forEach((row) => {
      const tr = node('tr');
      columns.forEach((c) => tr.appendChild(node('td', '', fmt(row && row[c]))));
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    section.appendChild(wrap);
    let note = `工具返回 ${rows.length} 行，页面显示 ${Math.min(maxRows, rows.length)} 行。`;
    if (data.truncated) note += ' 注意：查询结果在后端被截断，不代表完整数据。';
    if (rows.length > maxRows) note += ' 页面展示数量已限制；原始结果不变。';
    section.appendChild(node('p', 'table-note', note));
    container.appendChild(section);
  }
  function displayChart(container, data) {
    const rows = data.rows;
    if (!Array.isArray(rows) || rows.length < 2 || !rows[0] || typeof rows[0] !== 'object') return;
    const columns = columnsOf(data);
    const numeric = columns.filter((c) => rows.every((r) => r && typeof r[c] === 'number' && Number.isFinite(r[c]) && r[c] >= 0) && !/(?:^id$|_id$)/i.test(c));
    const dimension = ['purchase_month', 'order_date', 'month', 'date', 'customer_state', 'order_status'].find((c) => columns.includes(c) && !numeric.includes(c))
      || columns.find((c) => !numeric.includes(c));
    if (!numeric.length || !dimension) return;
    const field = ['late_rate_pct', 'order_count', 'delivered_with_dates', 'late_orders'].find((c) => numeric.includes(c)) || numeric[0];
    const section = node('section', 'result-block');
    const toolbar = node('div', 'chart-toolbar');
    toolbar.appendChild(node('h3', 'block-title', '数据可视化'));
    const opts = node('div', 'chart-options');
    const select = node('select');
    select.setAttribute('aria-label', '选择图表指标');
    numeric.forEach((key) => {
      const option = node('option', '', columnNames[key] || key);
      option.value = key;
      select.appendChild(option);
    });
    select.value = field;
    const bar = node('button', '', '柱状图');
    bar.type = 'button';
    const line = node('button', '', '折线图');
    line.type = 'button';
    let type = 'bar';
    const graph = node('div', 'chart-container');
    graph.setAttribute('role', 'region');
    graph.setAttribute('aria-label', '查询数据可视化图表');
    function draw() {
      const key = select.value;
      graph.replaceChildren();
      bar.setAttribute('aria-pressed', type === 'bar' ? 'true' : 'false');
      line.setAttribute('aria-pressed', type === 'line' ? 'true' : 'false');
      const subset = rows.slice(0, 24);
      const values = subset.map((r) => r[key]);
      const max = Math.max(...values, 0);
      const top = max > 0 ? max * 1.08 : 1;
      const W = 840, H = 284, L = 59, R = 16, T = 17, B = 55;
      const plotWidth = W - L - R, plotHeight = H - T - B;
      const canvas = svg('svg', {viewBox:`0 0 ${W} ${H}`, role:'img', 'aria-label': `${columnNames[key] || key}，前 ${subset.length} 行${type === 'bar' ? '柱状图' : '折线图'}`});
      for (let tick = 0; tick <= 4; tick++) {
        const y = T + (plotHeight * tick / 4);
        canvas.appendChild(svg('line', {x1:L,y1:y,x2:W-R,y2:y,stroke:'#e5ebf4','stroke-width':1}));
        canvas.appendChild(svg('text', {x:L-8,y:y+4,'text-anchor':'end',fill:'#8292a9','font-size':11}, fmt(top*(4-tick)/4)));
      }
      const step = plotWidth / subset.length;
      let path = '';
      subset.forEach((item, i) => {
        const x = L + step*(i+0.5);
        const y = T + plotHeight * (1 - values[i]/top);
        if (type === 'bar') {
          const rect = svg('rect', {x:x-step*.32,y:y,width:step*.64,height:Math.max(0,T+plotHeight-y),rx:2,fill:'#4f80e2'});
          rect.appendChild(svg('title', {}, `${fmt(item[dimension])}：${fmt(values[i])}`));
          canvas.appendChild(rect);
        } else {
          path += `${i===0?'M':'L'}${x.toFixed(2)},${y.toFixed(2)} `;
        }
        const labelEvery = Math.max(1, Math.ceil(subset.length / 7));
        if (i % labelEvery === 0 || i === subset.length-1) {
          const caption = String(item[dimension] ?? '').slice(0,13);
          canvas.appendChild(svg('text', {x,y:H-29,'text-anchor':'middle',fill:'#7a8aa1','font-size':10}, caption));
        }
      });
      if (type === 'line') {
        canvas.appendChild(svg('path', {d:path,fill:'none',stroke:'#3069d9','stroke-width':2.6,'stroke-linecap':'round','stroke-linejoin':'round'}));
        subset.forEach((row, i) => {
          const x = L + step*(i+0.5);
          const y = T + plotHeight*(1-values[i]/top);
          const dot = svg('circle', {cx:x,cy:y,r:3.6,fill:'#3069d9'});
          dot.appendChild(svg('title',{},`${fmt(row[dimension])}：${fmt(values[i])}`));
          canvas.appendChild(dot);
        });
      }
      graph.appendChild(canvas);
    }
    select.addEventListener('change', draw);
    bar.addEventListener('click', () => { type='bar'; draw(); });
    line.addEventListener('click', () => { type='line'; draw(); });
    opts.append(select, bar, line);
    toolbar.appendChild(opts);
    section.append(toolbar, graph);
    let legend = `${columnNames[dimension] || dimension} × ${columnNames[field] || field}；直接绘制工具返回的数据，不进行重新统计。`;
    if (rows.length > 24) legend += ' 图表仅展示前24行。';
    if (data.truncated) legend += ' 数据可能不完整。';
    if (numeric.includes('late_rate_pct') && rows.some((r) => typeof r.delivered_with_dates === 'number' && r.delivered_with_dates < 30)) {
      legend += ' 注意：部分月份有效订单少于30笔，延期率可能出现极端值；请结合订单数解读。';
    }
    section.appendChild(node('p', 'chart-legend', legend));
    container.appendChild(section);
    draw();
  }
  function renderResponse(data) {
    const card = node('article', 'answer-card');
    if (!data || typeof data !== 'object') {
      card.appendChild(node('p','answer-text','服务器返回格式不正确。'));
      return card;
    }
    const status = Object.hasOwn(statuses, data.status) ? data.status : 'error';
    const head = node('div', 'response-heading');
    head.append(node('span', 'answer-avatar', 'D'), node('strong', '', 'DataPilot Agent'));
    head.append(badge(statuses[status], status));
    if (data.selected_tool && toolNames[data.selected_tool]) head.append(node('span', 'response-tag', toolNames[data.selected_tool]));
    if (data.context_used === true) head.append(node('span', 'response-tag', '已继承上下文'));
    card.appendChild(head);
    card.appendChild(node('p', 'answer-text', typeof data.answer === 'string' ? data.answer : '未返回文字回答。'));
    const meta = node('div', 'response-metadata');
    if (data.session_turn) meta.append(node('span', '', `第 ${data.session_turn} 轮`));
    if (data.context_used && data.effective_question) meta.append(node('span', '', `实际查询：${data.effective_question}`));
    card.appendChild(meta);
    if (status === 'partial') card.appendChild(node('p', 'response-warning', '查询结果已截断，不应视为完整数据。'));
    if (status === 'needs_review') card.appendChild(node('p', 'response-warning', '语义风险尚未解除；未取得可展示的已执行结果。'));
    const result = data.tool_result;
    if (result && typeof result === 'object' && (status === 'ok' || status === 'partial')) {
      displayPrediction(card, result);
      if (result.data && typeof result.data === 'object' && Array.isArray(result.data.rows)) {
        displayChart(card, result.data);
        displayTable(card, result.data);
      }
    }
    displayAudit(card, data);
    return card;
  }
  function httpError(response, data) {
    if (response.status === 404) return 'V3 实验接口未启用，请确认 DATAPILOT_V3_ENABLED=1 并重启本机服务。';
    if (response.status === 403) return 'V3 仅允许从本机访问。';
    if (response.status === 409) return '会话已过期或服务器已重启。请点击“新建会话”后重新输入完整问题。';
    if (response.status === 422) return '请求参数无效，请检查问题长度及会话信息。';
    if (typeof data?.detail === 'string') return data.detail;
    return `HTTP ${response.status}：请求没有完成。`;
  }
  async function send() {
    if (busy) return;
    const question = input.value.trim();
    if (!question) { input.focus(); return; }
    clearError();
    setBusy(true);
    const loading = addTurn(question);
    input.value = '';
    try {
      const payload = { question };
      if (conversationId) payload.conversation_id = conversationId;
      const response = await fetch('/api/v3/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        cache: 'no-store',
        body: JSON.stringify(payload),
      });
      let data;
      try { data = await response.json(); }
      catch (_) { throw new Error('服务器未返回有效 JSON。'); }
      if (!response.ok) {
        if (response.status === 409) {
          conversationId = null;
          sessionState.textContent = '会话已过期';
        }
        throw new Error(httpError(response, data));
      }
      if (!data || typeof data !== 'object' || typeof data.conversation_id !== 'string') {
        throw new Error('API 响应缺少会话 ID，请检查服务版本。');
      }
      conversationId = data.conversation_id;
      sessionState.textContent = `会话第 ${Number.isInteger(data.session_turn) ? data.session_turn : '?'} 轮`;
      loading.replaceWith(renderResponse(data));
    } catch (error) {
      const message = error && error.message ? error.message : '网络或服务器异常。';
      showError(message);
      loading.replaceWith(renderResponse({status:'error',answer:`请求失败：${message}`}));
    } finally {
      setBusy(false);
      input.focus();
      scrollEnd();
    }
  }
  form.addEventListener('submit', (event) => { event.preventDefault(); send(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault(); send();
    }
  });
  examples.forEach((button) => button.addEventListener('click', () => {
    if (busy) return;
    input.value = button.dataset.question || '';
    input.focus();
  }));
  newChatButtons.forEach((button) => button.addEventListener('click', newConversation));
})();
