/**
 * 计算换算分类的纯前端面板。
 *
 * 这一整类工具不碰服务器：公式都在浏览器里算，不上传任何数据，也不需要
 * 后端新增路由。app.js 在 renderToolPanel 里看到 config.custom === "calc"
 * 就把挂载点交给 ToolboxCustom.calc，本文件负责渲染表单并实时出结果。
 */
(function () {
  'use strict';

  // ============================ 单位换算表 ============================
  // 每个单位给出「转基准值 / 从基准值转回」两个函数，新增单位只需加一行。
  const UNITS = [
    { id: 'mm', label: '毫米', group: '长度', to: v => v / 1000, from: v => v * 1000 },
    { id: 'cm', label: '厘米', group: '长度', to: v => v / 100, from: v => v * 100 },
    { id: 'm', label: '米', group: '长度', to: v => v, from: v => v },
    { id: 'km', label: '千米', group: '长度', to: v => v * 1000, from: v => v / 1000 },
    { id: 'inch', label: '英寸', group: '长度', to: v => v * 0.0254, from: v => v / 0.0254 },
    { id: 'ft', label: '英尺', group: '长度', to: v => v * 0.3048, from: v => v / 0.3048 },
    { id: 'mile', label: '英里', group: '长度', to: v => v * 1609.344, from: v => v / 1609.344 },

    { id: 'mg', label: '毫克', group: '重量', to: v => v / 1e6, from: v => v * 1e6 },
    { id: 'g', label: '克', group: '重量', to: v => v / 1000, from: v => v * 1000 },
    { id: 'kg', label: '千克', group: '重量', to: v => v, from: v => v },
    { id: 't', label: '吨', group: '重量', to: v => v * 1000, from: v => v / 1000 },
    { id: 'lb', label: '磅', group: '重量', to: v => v * 0.45359237, from: v => v / 0.45359237 },
    { id: 'oz', label: '盎司', group: '重量', to: v => v * 0.0283495, from: v => v / 0.0283495 },

    { id: 'cm2', label: '平方厘米', group: '面积', to: v => v / 10000, from: v => v * 10000 },
    { id: 'm2', label: '平方米', group: '面积', to: v => v, from: v => v },
    { id: 'km2', label: '平方千米', group: '面积', to: v => v * 1e6, from: v => v / 1e6 },
    { id: 'ha', label: '公顷', group: '面积', to: v => v * 10000, from: v => v / 10000 },
    { id: 'mu', label: '亩', group: '面积', to: v => v * 666.6667, from: v => v / 666.6667 },
    { id: 'acre', label: '英亩', group: '面积', to: v => v * 4046.86, from: v => v / 4046.86 },

    { id: 'ml', label: '毫升', group: '体积', to: v => v / 1000, from: v => v * 1000 },
    { id: 'l', label: '升', group: '体积', to: v => v, from: v => v },
    { id: 'm3', label: '立方米', group: '体积', to: v => v * 1000, from: v => v / 1000 },
    { id: 'gal', label: '加仑(美)', group: '体积', to: v => v * 3.78541, from: v => v / 3.78541 },

    { id: 'B', label: '字节', group: '字节', to: v => v, from: v => v },
    { id: 'KB', label: 'KB', group: '字节', to: v => v * 1024, from: v => v / 1024 },
    { id: 'MB', label: 'MB', group: '字节', to: v => v * 1024 ** 2, from: v => v / 1024 ** 2 },
    { id: 'GB', label: 'GB', group: '字节', to: v => v * 1024 ** 3, from: v => v / 1024 ** 3 },
    { id: 'TB', label: 'TB', group: '字节', to: v => v * 1024 ** 4, from: v => v / 1024 ** 4 },

    { id: 'sec', label: '秒', group: '时间', to: v => v, from: v => v },
    { id: 'min', label: '分钟', group: '时间', to: v => v * 60, from: v => v / 60 },
    { id: 'hour', label: '小时', group: '时间', to: v => v * 3600, from: v => v / 3600 },
    { id: 'day', label: '天', group: '时间', to: v => v * 86400, from: v => v / 86400 },
    { id: 'week', label: '周', group: '时间', to: v => v * 604800, from: v => v / 604800 },

    { id: 'C', label: '摄氏度', group: '温度', temp: true, to: v => v, from: v => v },
    { id: 'F', label: '华氏度', group: '温度', temp: true, to: v => (v - 32) * 5 / 9, from: v => v * 9 / 5 + 32 },
    { id: 'K', label: '开尔文', group: '温度', temp: true, to: v => v - 273.15, from: v => v + 273.15 },
  ];

  function unitOptions() {
    const groups = [];
    UNITS.forEach(u => {
      let g = groups.find(x => x.label === u.group);
      if (!g) { g = { label: u.group, items: [] }; groups.push(g); }
      g.items.push(u);
    });
    return groups.map(g =>
      `<optgroup label="${g.label}">${
        g.items.map(u => `<option value="${u.id}">${u.label}</option>`).join('')
      }</optgroup>`).join('');
  }

  // ============================ 安全算式求值（不用 eval） ============================
  function calcExpr(src) {
    const s = String(src || '').replace(/\s+/g, '');
    if (!s) throw new Error('请输入算式');
    if (!/^[0-9+\-*/%^().]+$/.test(s)) throw new Error('只能包含数字与 + - * / % ^ ( )');

    let pos = 0;
    function peek() { return s[pos]; }
    function eat(ch) { if (s[pos] === ch) { pos++; return true; } return false; }

    function parseExpr() {
      let v = parseTerm();
      for (;;) {
        if (eat('+')) v += parseTerm();
        else if (eat('-')) v -= parseTerm();
        else return v;
      }
    }
    function parseTerm() {
      let v = parsePower();
      for (;;) {
        if (eat('*')) v *= parsePower();
        else if (eat('/')) { const d = parsePower(); if (d === 0) throw new Error('除数不能为 0'); v /= d; }
        else if (eat('%')) { const d = parsePower(); if (d === 0) throw new Error('取余的除数不能为 0'); v %= d; }
        else return v;
      }
    }
    function parsePower() {
      const base = parseUnary();
      if (eat('^')) return Math.pow(base, parseUnary()); // 右结合
      return base;
    }
    function parseUnary() {
      if (eat('-')) return -parseUnary();
      if (eat('+')) return parseUnary();
      return parseAtom();
    }
    function parseAtom() {
      if (eat('(')) {
        const v = parseExpr();
        if (!eat(')')) throw new Error('括号不匹配');
        return v;
      }
      const start = pos;
      while (pos < s.length && (s[pos] >= '0' && s[pos] <= '9' || s[pos] === '.')) pos++;
      if (start === pos) throw new Error('算式格式不正确');
      const n = parseFloat(s.slice(start, pos));
      if (isNaN(n)) throw new Error('数字格式不正确');
      return n;
    }

    const result = parseExpr();
    if (pos !== s.length) throw new Error('算式有无法解析的部分');
    if (!isFinite(result)) throw new Error('结果不是一个有限数');
    return result;
  }

  // ============================ 人民币大写 ============================
  const RMB_DIGITS = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖'];
  const RMB_UNITS = ['', '拾', '佰', '仟'];
  const RMB_SECTIONS = ['', '万', '亿', '万亿'];

  function rmbUpper(amount) {
    const neg = amount < 0;
    let v = Math.abs(Math.round(amount * 100) / 100);
    if (v === 0) return '零元整';
    const yuan = Math.floor(v);
    const jiao = Math.floor((v * 10) % 10);
    const fen = Math.round((v * 100) % 10);

    function intPart(n) {
      if (n === 0) return '';
      const sections = [];
      let idx = 0;
      while (n > 0 && idx < RMB_SECTIONS.length) {
        sections.push({ sec: n % 10000, unit: RMB_SECTIONS[idx] });
        n = Math.floor(n / 10000);
        idx++;
      }
      let out = '';
      for (let i = sections.length - 1; i >= 0; i--) {
        const { sec, unit } = sections[i];
        let buf = '';
        let zero = false;
        let hasDigit = false;
        for (let p = 3; p >= 0; p--) {
          const d = Math.floor(sec / Math.pow(10, p)) % 10;
          if (d === 0) {
            zero = true;
          } else {
            if (zero && hasDigit) buf += '零';
            buf += RMB_DIGITS[d] + RMB_UNITS[p];
            zero = false;
            hasDigit = true;
          }
        }
        if (hasDigit) {
          out += buf + unit;
          // 本节不足四位且后面还有更低节时补零（如 100000005）
          if (i > 0 && sections[i - 1].sec > 0 && sections[i - 1].sec < 1000) out += '零';
        }
      }
      return out.replace(/零+$/, '');
    }

    let out = (neg ? '负' : '') + intPart(yuan) + '元';
    if (jiao === 0 && fen === 0) {
      out += '整';
    } else {
      out += (jiao ? RMB_DIGITS[jiao] + '角' : (fen ? '零' : ''));
      if (fen) out += RMB_DIGITS[fen] + '分';
    }
    return out;
  }

  // ============================ 小工具 ============================
  const num = v => { const n = parseFloat(v); return isFinite(n) ? n : 0; };
  const money = v => (Math.round(v * 100) / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const plain = v => {
    const a = Math.abs(v);
    if (a === 0) return '0';
    if (a >= 1e12 || a < 1e-6) return v.toExponential(6);
    return String(Math.round(v * 1e6) / 1e6);
  };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function addDays(dateStr, days) {
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d.getTime())) return null;
    d.setDate(d.getDate() + days);
    return d;
  }
  function addWorkdays(dateStr, days) {
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d.getTime())) return null;
    let left = Math.abs(days);
    const step = days >= 0 ? 1 : -1;
    while (left > 0) {
      d.setDate(d.getDate() + step);
      const w = d.getDay();
      if (w !== 0 && w !== 6) left--;
    }
    return d;
  }
  const fmtDate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  // ============================ 各工具的字段与计算 ============================
  const SPECS = {
    calc_unit: {
      fields: [
        { name: 'value', label: '数值', type: 'number', value: 1, step: 'any' },
        { name: 'from', label: '原单位', type: 'raw', html: `<select name="from">${unitOptions()}</select>` },
      ],
      calc(v) {
        const u = UNITS.find(x => x.id === v.from);
        if (!u) return '<div class="calc-empty">请选择单位</div>';
        const base = u.to(num(v.value));
        const rows = UNITS.filter(x => x.group === u.group).map(x => {
          const val = x.from(base);
          return `<div class="calc-row${x.id === u.id ? ' is-current' : ''}">
            <span class="calc-row-k">${x.label}</span>
            <span class="calc-row-v">${plain(val)}</span></div>`;
        }).join('');
        return `<div class="calc-grid">${rows}</div>`;
      }
    },

    calc_base: {
      fields: [
        { name: 'value', label: '数值', type: 'text', value: '255', placeholder: '支持 0x / 0b / 0o 前缀' },
        { name: 'from', label: '原进制', type: 'select', options: [
          { value: '2', label: '二进制 (2)' }, { value: '8', label: '八进制 (8)' },
          { value: '10', label: '十进制 (10)' }, { value: '16', label: '十六进制 (16)' },
          { value: '36', label: '三十六进制 (36)' },
        ], value: '10' },
      ],
      calc(v) {
        let raw = String(v.value || '').trim();
        if (!raw) return '<div class="calc-empty">请输入数值</div>';
        let base = parseInt(v.from, 10);
        if (/^0x/i.test(raw)) { raw = raw.slice(2); base = 16; }
        else if (/^0b/i.test(raw)) { raw = raw.slice(2); base = 2; }
        else if (/^0o/i.test(raw)) { raw = raw.slice(2); base = 8; }
        const n = parseInt(raw, base);
        if (!isFinite(n)) return '<div class="calc-empty">无法解析该数值（请检查与原进制是否匹配）</div>';
        const rows = [
          ['二进制', n.toString(2)], ['八进制', n.toString(8)],
          ['十进制', n.toString(10)], ['十六进制', n.toString(16).toUpperCase()],
          ['三十六进制', n.toString(36).toUpperCase()],
        ];
        return `<div class="calc-grid">${rows.map(([k, val]) =>
          `<div class="calc-row"><span class="calc-row-k">${k}</span><span class="calc-row-v">${esc(val)}</span></div>`
        ).join('')}</div>`;
      }
    },

    calc_loan: {
      fields: [
        { name: 'amount', label: '贷款金额', type: 'number', value: 1000000, unit: '元', step: 'any' },
        { name: 'years', label: '贷款年限', type: 'number', value: 30, unit: '年', step: 'any' },
        { name: 'rate', label: '年利率', type: 'number', value: 3.5, unit: '%', step: 'any' },
        { name: 'method', label: '还款方式', type: 'select', options: [
          { value: 'equal_total', label: '等额本息（月供固定）' },
          { value: 'equal_principal', label: '等额本金（月供递减）' },
        ], value: 'equal_total' },
      ],
      calc(v) {
        const P = num(v.amount), years = num(v.years), ann = num(v.rate);
        const n = Math.round(years * 12);
        if (P <= 0 || n <= 0) return '<div class="calc-empty">请填写正确的金额与年限</div>';
        const r = ann / 100 / 12;
        let totalInterest = 0, first = 0, last = 0, schedule = '';

        if (v.method === 'equal_principal') {
          const principal = P / n;
          first = principal + P * r;
          last = principal + principal * r;
          let remain = P;
          const rows = [];
          for (let i = 1; i <= Math.min(n, 12); i++) {
            const interest = remain * r;
            rows.push([i, principal + interest, principal, interest]);
            remain -= principal;
            totalInterest += interest;
          }
          // 剩余期数直接按等差数列求和，避免大循环
          if (n > 12) {
            let rm = remain;
            for (let i = 13; i <= n; i++) { totalInterest += rm * r; rm -= principal; }
          }
          schedule = rows.map(([i, pay, pr, it]) =>
            `<tr><td>${i}</td><td>${money(pay)}</td><td>${money(pr)}</td><td>${money(it)}</td></tr>`).join('');
        } else {
          const monthly = r === 0 ? P / n : P * r * Math.pow(1 + r, n) / (Math.pow(1 + r, n) - 1);
          first = last = monthly;
          totalInterest = monthly * n - P;
          let remain = P;
          const rows = [];
          for (let i = 1; i <= Math.min(n, 12); i++) {
            const it = remain * r;
            const pr = monthly - it;
            rows.push([i, monthly, pr, it]);
            remain -= pr;
          }
          schedule = rows.map(([i, pay, pr, it]) =>
            `<tr><td>${i}</td><td>${money(pay)}</td><td>${money(pr)}</td><td>${money(it)}</td></tr>`).join('');
        }

        return `<div class="calc-grid">
            <div class="calc-row"><span class="calc-row-k">首月月供</span><span class="calc-row-v is-strong">${money(first)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">末月月供</span><span class="calc-row-v">${money(last)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">还款总期数</span><span class="calc-row-v">${n} 期</span></div>
            <div class="calc-row"><span class="calc-row-k">支付总利息</span><span class="calc-row-v is-strong">${money(totalInterest)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">本息合计</span><span class="calc-row-v">${money(P + totalInterest)} 元</span></div>
          </div>
          <div class="calc-table-title">前 ${Math.min(n, 12)} 期还款计划</div>
          <table class="calc-table"><thead><tr><th>期数</th><th>月供</th><th>本金</th><th>利息</th></tr></thead><tbody>${schedule}</tbody></table>`;
      }
    },

    calc_invest: {
      fields: [
        { name: 'mode', label: '投入方式', type: 'select', options: [
          { value: 'once', label: '一次性投入' },
          { value: 'monthly', label: '每月定投' },
        ], value: 'once' },
        { name: 'amount', label: '金额', type: 'number', value: 100000, unit: '元', step: 'any' },
        { name: 'rate', label: '预期年化收益率', type: 'number', value: 6, unit: '%', step: 'any' },
        { name: 'years', label: '投资年限', type: 'number', value: 10, unit: '年', step: 'any' },
      ],
      calc(v) {
        const A = num(v.amount), ann = num(v.rate), years = num(v.years);
        if (A <= 0 || years <= 0) return '<div class="calc-empty">请填写正确的金额与年限</div>';
        const months = Math.round(years * 12);
        const mr = ann / 100 / 12;
        let principal, finalValue;
        if (v.mode === 'monthly') {
          principal = A * months;
          finalValue = mr === 0 ? principal : A * ((Math.pow(1 + mr, months) - 1) / mr) * (1 + mr);
        } else {
          principal = A;
          finalValue = A * Math.pow(1 + mr, months);
        }
        const profit = finalValue - principal;
        return `<div class="calc-grid">
            <div class="calc-row"><span class="calc-row-k">累计投入</span><span class="calc-row-v">${money(principal)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">期末总值</span><span class="calc-row-v is-strong">${money(finalValue)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">累计收益</span><span class="calc-row-v is-strong">${money(profit)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">收益率</span><span class="calc-row-v">${(profit / principal * 100).toFixed(2)}%</span></div>
          </div>
          <div class="calc-note">按月末投入、月复利估算，未计税费与申购赎回费用。</div>`;
      }
    },

    calc_social: {
      fields: [
        { name: 'base', label: '缴费基数', type: 'number', value: 10000, unit: '元', step: 'any' },
        { name: 'fund', label: '公积金个人比例', type: 'number', value: 12, unit: '%', step: 'any' },
      ],
      calc(v) {
        const base = num(v.base), fund = num(v.fund);
        // 比例以常见口径为例，各地政策不同，界面上明确提示以当地为准
        const items = [
          ['养老保险', 8, 16], ['医疗保险', 2, 8], ['失业保险', 0.5, 0.5],
          ['工伤保险', 0, 0.4], ['生育保险', 0, 0.8], ['住房公积金', fund, fund],
        ];
        let pSum = 0, cSum = 0;
        const rows = items.map(([name, p, c]) => {
          const pv = base * p / 100, cv = base * c / 100;
          pSum += pv; cSum += cv;
          return `<tr><td>${name}</td><td>${p}%</td><td>${money(pv)}</td><td>${c}%</td><td>${money(cv)}</td></tr>`;
        }).join('');
        return `<table class="calc-table">
            <thead><tr><th>项目</th><th>个人比例</th><th>个人金额</th><th>单位比例</th><th>单位金额</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
          <div class="calc-grid">
            <div class="calc-row"><span class="calc-row-k">个人合计（税前扣款）</span><span class="calc-row-v is-strong">${money(pSum)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">单位合计</span><span class="calc-row-v is-strong">${money(cSum)} 元</span></div>
            <div class="calc-row"><span class="calc-row-k">缴费基数</span><span class="calc-row-v">${money(base)} 元</span></div>
          </div>
          <div class="calc-note">默认比例为常见口径示例，各地基数上下限与比例不同，请以当地社保政策为准。</div>`;
      }
    },

    calc_rmb: {
      fields: [
        { name: 'amount', label: '金额', type: 'number', value: 1234.56, unit: '元', step: 'any' },
      ],
      calc(v) {
        const a = num(v.amount);
        return `<div class="calc-big">${esc(rmbUpper(a))}</div>
          <div class="calc-note">原金额：${money(a)} 元</div>`;
      }
    },

    calc_bmi: {
      fields: [
        { name: 'height', label: '身高', type: 'number', value: 170, unit: 'cm', step: 'any' },
        { name: 'weight', label: '体重', type: 'number', value: 65, unit: 'kg', step: 'any' },
      ],
      calc(v) {
        const h = num(v.height) / 100, w = num(v.weight);
        if (h <= 0 || w <= 0) return '<div class="calc-empty">请填写正确的身高体重</div>';
        const bmi = w / (h * h);
        let tag = '正常', cls = 'is-ok';
        if (bmi < 18.5) { tag = '偏瘦'; cls = 'is-warn'; }
        else if (bmi < 24) { tag = '正常'; cls = 'is-ok'; }
        else if (bmi < 28) { tag = '超重'; cls = 'is-warn'; }
        else { tag = '肥胖'; cls = 'is-danger'; }
        return `<div class="calc-big">${bmi.toFixed(1)} <span class="calc-tag ${cls}">${tag}</span></div>
          <div class="calc-note">中国成人参考：偏瘦 &lt;18.5 · 正常 18.5-23.9 · 超重 24-27.9 · 肥胖 ≥28</div>`;
      }
    },

    calc_date: {
      fields: [
        { name: 'mode', label: '计算方式', type: 'select', options: [
          { value: 'diff', label: '两个日期相差多少天' },
          { value: 'add', label: '从某天推算 N 天之后' },
        ], value: 'diff' },
        { name: 'a', label: '日期 A', type: 'date', value: '' },
        { name: 'b', label: '日期 B / 天数', type: 'text', value: '', placeholder: 'diff 填日期，add 填天数' },
        { name: 'workday', label: '只算工作日（跳过周末）', type: 'checkbox', value: false },
      ],
      calc(v) {
        if (!v.a) return '<div class="calc-empty">请选择日期 A</div>';
        if (v.mode === 'diff') {
          if (!v.b) return '<div class="calc-empty">请填写日期 B</div>';
          const d1 = new Date(v.a + 'T00:00:00'), d2 = new Date(v.b + 'T00:00:00');
          if (isNaN(d1.getTime()) || isNaN(d2.getTime())) return '<div class="calc-empty">日期格式不正确</div>';
          const days = Math.round((d2 - d1) / 86400000);
          let extra = '';
          if (v.workday) {
            let wd = 0;
            const step = days >= 0 ? 1 : -1;
            const cur = new Date(d1);
            for (let i = 0; i < Math.abs(days); i++) {
              cur.setDate(cur.getDate() + step);
              const w = cur.getDay();
              if (w !== 0 && w !== 6) wd += step;
            }
            extra = `<div class="calc-row"><span class="calc-row-k">工作日天数</span><span class="calc-row-v is-strong">${wd} 天</span></div>`;
          }
          return `<div class="calc-grid">
              <div class="calc-row"><span class="calc-row-k">自然日天数</span><span class="calc-row-v is-strong">${days} 天</span></div>
              ${extra}
              <div class="calc-row"><span class="calc-row-k">约合</span><span class="calc-row-v">${(Math.abs(days) / 7).toFixed(1)} 周</span></div>
            </div>`;
        }
        const n = parseInt(v.b, 10);
        if (!isFinite(n)) return '<div class="calc-empty">请填写要推算的天数（可为负数）</div>';
        const d = v.workday ? addWorkdays(v.a, n) : addDays(v.a, n);
        if (!d) return '<div class="calc-empty">日期格式不正确</div>';
        return `<div class="calc-big">${fmtDate(d)}</div>
          <div class="calc-note">${v.a} ${n >= 0 ? '之后' : '之前'} ${Math.abs(n)} ${v.workday ? '个工作日' : '天'}</div>`;
      }
    },

    calc_expr: {
      fields: [
        { name: 'expr', label: '算式', type: 'text', value: '(1+2)*3^2-4/2', placeholder: '支持 + - * / % ^ 与括号' },
      ],
      history: [],
      calc(v, ctx) {
        const src = String(v.expr || '').trim();
        if (!src) return '<div class="calc-empty">请输入算式</div>';
        let res;
        try {
          res = calcExpr(src);
        } catch (e) {
          return `<div class="calc-error">⚠️ ${esc(e.message)}</div>`;
        }
        if (ctx && ctx.onResult) ctx.onResult(src, res);
        return `<div class="calc-big">${plain(res)}</div>`;
      }
    },
  };

  // ============================ 渲染 ============================
  const CalcTools = {
    specs: SPECS,

    fieldHtml(f) {
      if (f.type === 'raw') {
        return `<div class="calc-field"><label>${esc(f.label)}</label>${f.html}</div>`;
      }
      if (f.type === 'select') {
        const opts = f.options.map(o =>
          `<option value="${o.value}"${o.value === f.value ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
        return `<div class="calc-field"><label>${esc(f.label)}</label><select name="${f.name}">${opts}</select></div>`;
      }
      if (f.type === 'checkbox') {
        return `<div class="calc-field calc-field-check">
          <label class="checkbox-wrap"><input type="checkbox" name="${f.name}"${f.value ? ' checked' : ''}><span>${esc(f.label)}</span></label>
        </div>`;
      }
      const unit = f.unit ? `<span class="calc-unit">${esc(f.unit)}</span>` : '';
      const attrs = f.type === 'number' ? ` step="${f.step || 'any'}"` : '';
      return `<div class="calc-field"><label>${esc(f.label)}${unit}</label>
        <input type="${f.type}" name="${f.name}" value="${esc(f.value == null ? '' : f.value)}"
               placeholder="${esc(f.placeholder || '')}"${attrs}></div>`;
    },

    render(toolId, mount) {
      const spec = this.specs[toolId];
      if (!spec) {
        mount.innerHTML = '<div class="calc-error">⚠️ 该计算工具尚未实现</div>';
        return;
      }
      mount.innerHTML = `
        <div class="calc-form">${spec.fields.map(f => this.fieldHtml(f)).join('')}</div>
        <div class="calc-out" id="calc-out"></div>
        ${toolId === 'calc_expr' ? '<div class="calc-history" id="calc-history"></div>' : ''}
      `;

      const out = mount.querySelector('#calc-out');
      const historyEl = mount.querySelector('#calc-history');
      const history = [];

      const run = () => {
        const v = {};
        spec.fields.forEach(f => {
          const el = mount.querySelector(`[name="${f.name}"]`);
          if (!el) return;
          v[f.name] = el.type === 'checkbox' ? el.checked : el.value;
        });
        const ctx = {
          onResult(src, res) {
            if (history.length === 0 || history[0].src !== src) {
              history.unshift({ src, res });
              history.splice(8);
              if (historyEl) {
                historyEl.innerHTML = '<div class="calc-table-title">历史</div>' + history.map(h =>
                  `<div class="calc-hist-row"><span>${esc(h.src)}</span><b>${plain(h.res)}</b></div>`).join('');
              }
            }
          }
        };
        try {
          out.innerHTML = spec.calc(v, ctx) || '';
        } catch (e) {
          out.innerHTML = `<div class="calc-error">⚠️ ${esc(e.message || String(e))}</div>`;
        }
      };

      mount.querySelectorAll('input, select').forEach(el => {
        el.addEventListener('input', run);
        el.addEventListener('change', run);
      });
      run();
    }
  };

  window.ToolboxCustom = window.ToolboxCustom || {};
  window.ToolboxCustom.calc = function (toolId, mount) { CalcTools.render(toolId, mount); };
  window.CalcTools = CalcTools;
})();
