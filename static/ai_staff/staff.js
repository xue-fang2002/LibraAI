/* AI 员工 · 对话界面
   - 前端只负责渲染，取数全部走 /staff/api/*
   - 过程流用 SSE：start / step / tool / final / error / done / timeout
   - 当前员工由模板注入：window.STAFF_ID / STAFF_NAME / STAFF_SAMPLES
*/
(function () {
    'use strict';

    var chat = document.getElementById('staffChat');
    var ta = document.getElementById('staffInput');
    var sendBtn = document.getElementById('staffSend');
    var stopBtn = document.getElementById('staffStop');
    var hint = document.getElementById('staffHint');
    var flags = document.getElementById('staffFlags');
    if (!chat || !ta || !sendBtn) return;

    var STAFF_ID = window.STAFF_ID || 'ops';
    var STAFF_NAME = window.STAFF_NAME || 'AI 员工';
    var SAMPLES = window.STAFF_SAMPLES || [];
    var FORM = window.STAFF_FORM || null;

    var es = null;
    var taskId = '';

    function scrollBottom() { chat.scrollTop = chat.scrollHeight; }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = text;
        return n;
    }

    function emptyState() {
        chat.innerHTML = '';
        var box = el('div', 'staff-empty');
        if (SAMPLES.length) {
            var html = '问问' + STAFF_NAME + '，例如：<br>';
            for (var i = 0; i < SAMPLES.length; i++) {
                html += '<code>' + SAMPLES[i].replace(/[<>&]/g, function (c) {
                    return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c];
                }) + '</code><br>';
            }
            box.innerHTML = html;
        } else {
            box.textContent = '向' + STAFF_NAME + '提问吧。';
        }
        chat.appendChild(box);
    }

    function addUser(text) {
        var wrap = el('div', 'staff-msg me');
        wrap.appendChild(el('div', 'staff-msg-who', '我'));
        wrap.appendChild(el('div', 'staff-bubble', text));
        chat.appendChild(wrap);
        scrollBottom();
    }

    function addAssistant() {
        // 只移除空状态提示，不能整段清空——否则会把刚插入的用户消息一起清掉
        var emptyEl = chat.querySelector('.staff-empty');
        if (emptyEl) emptyEl.remove();
        var wrap = el('div', 'staff-msg');
        wrap.appendChild(el('div', 'staff-msg-who', STAFF_NAME));
        var body = el('div', 'staff-bubble');
        var thinking = el('div', 'staff-thinking');
        var dots = el('span', 'staff-dots');
        dots.appendChild(el('i'));
        dots.appendChild(el('i'));
        dots.appendChild(el('i'));
        thinking.appendChild(dots);
        thinking.appendChild(el('span', '', '取数中…'));
        body.appendChild(thinking);
        var steps = el('div', 'staff-steps');
        wrap.appendChild(steps);
        wrap.appendChild(body);
        chat.appendChild(wrap);
        scrollBottom();
        return { body: body, steps: steps, thinking: thinking };
    }

    function addStep(steps, text) {
        steps.appendChild(el('div', 'staff-step', text));
        scrollBottom();
    }

    function short(v, n) {
        var s = typeof v === 'string' ? v : JSON.stringify(v);
        if (s === undefined) s = String(v);
        return s.length > n ? s.slice(0, n) + '…' : s;
    }

    function addTool(steps, ev) {
        // 排盘结果自带结构化报告：渲染成卡片，别甩一坨 JSON 给用户
        if (ev.result && ev.result.report && ev.result.report.chart) {
            addBaziCard(steps, ev.result.report);
            return;
        }
        var box = el('div', 'staff-tool');
        var head = el('div', 'staff-tool-head');
        head.appendChild(el('span', 'staff-tool-name', ev.name || 'tool'));
        head.appendChild(el('span', 'staff-tool-args', short(ev.args || {}, 120)));
        var badge = el('span', 'staff-tool-badge' + (ev.ok ? '' : ' bad'), ev.ok ? '成功' : '失败');
        head.appendChild(badge);
        var body = el('div', 'staff-tool-body');
        var pre = el('pre', '', JSON.stringify(ev.result, null, 2));
        body.appendChild(pre);
        box.appendChild(head);
        box.appendChild(body);
        head.addEventListener('click', function () {
            box.classList.toggle('open');
        });
        steps.appendChild(box);
        scrollBottom();
    }

    function finishAnswer(slot, text) {
        if (slot.thinking && slot.thinking.parentNode) {
            slot.thinking.parentNode.removeChild(slot.thinking);
        }
        if (window.AiMarkdown && typeof window.AiMarkdown.renderInto === 'function') {
            try {
                window.AiMarkdown.renderInto(slot.body, text);
                scrollBottom();
                return;
            } catch (e) { /* 回落到纯文本 */ }
        }
        slot.body.textContent = text;
        scrollBottom();
    }

    // ---------------------------------------------------------- 排盘结果卡
    var WX_CLS = { '木': 'wood', '火': 'fire', '土': 'earth', '金': 'metal', '水': 'water' };

    function chip(k, v) {
        var c = el('div', 'bz-chip');
        c.appendChild(el('span', 'bz-chip-k', k));
        c.appendChild(el('span', 'bz-chip-v', String(v === undefined || v === null ? '-' : v)));
        return c;
    }

    function addBaziCard(steps, rep) {
        var ch = rep.chart || {}, e5 = rep.elements || {}, ss = rep.shi_shen || {};
        var box = el('div', 'staff-bazi');

        var row = el('div', 'bz-pillars');
        (ch.pillars || []).forEach(function (p) {
            var col = el('div', 'bz-pillar');
            col.appendChild(el('div', 'bz-p-label', p.label));
            col.appendChild(el('div', 'bz-p-gz', p.ganzhi || '-'));
            col.appendChild(el('div', 'bz-p-ss', p.shi_shen || '-'));
            row.appendChild(col);
        });
        box.appendChild(row);

        var dm = ch.day_master || {}, st = ch.strength || {};
        var meta = el('div', 'bz-meta');
        meta.appendChild(chip('日主', (dm.gan || '?') + '（' + (dm.element || '?') + '·' + (dm.polarity || '?') + '）'));
        meta.appendChild(chip('强弱', (st.verdict || '-') + '（' + (st.score === undefined ? '-' : st.score) + '）'));
        meta.appendChild(chip('最旺五行', e5.dominant || '-'));
        meta.appendChild(chip('缺', (e5.missing || []).join('、') || '无'));
        box.appendChild(meta);

        var counts = e5.counts || {}, maxn = 1;
        Object.keys(counts).forEach(function (k) { if ((counts[k] || 0) > maxn) maxn = counts[k]; });
        var bars = el('div', 'bz-bars');
        Object.keys(counts).forEach(function (k) {
            var item = el('div', 'bz-bar-item');
            item.appendChild(el('span', 'bz-bar-k', k));
            var track = el('div', 'bz-bar-track');
            var fill = el('div', 'bz-bar-fill wx-' + (WX_CLS[k] || 'other'));
            fill.style.width = Math.round(((counts[k] || 0) / maxn) * 100) + '%';
            track.appendChild(fill);
            item.appendChild(track);
            item.appendChild(el('span', 'bz-bar-n', String(counts[k] || 0)));
            bars.appendChild(item);
        });
        box.appendChild(bars);

        if ((ss.top || []).length) {
            var lead = el('div', 'bz-lead');
            lead.appendChild(el('span', 'bz-lead-t', '主导十神'));
            lead.appendChild(el('span', '', ss.top.join(' / ')));
            box.appendChild(lead);
        }
        if (rep.birth && rep.birth.true_solar_alert) {
            box.appendChild(el('div', 'bz-alert', rep.birth.true_solar_alert));
        }
        if (!rep.birth || rep.birth.hour_known === false) {
            box.appendChild(el('div', 'bz-alert', '未提供出生时辰，时柱缺失，结论按年月日三柱推算。'));
        }
        box.appendChild(el('div', 'bz-disc', rep.disclaimer || ''));
        steps.appendChild(box);
        scrollBottom();
    }

    // ---------------------------------------------------------- 表单型员工
    function todayStr() {
        var d = new Date();
        return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
    }

    function buildForm() {
        var box = document.getElementById('staffForm');
        if (!box || !FORM || !FORM.fields || !FORM.fields.length) return;
        box.hidden = false;

        var head = el('div', 'staff-form-head');
        head.appendChild(el('span', 'staff-form-title', FORM.title || '填写信息'));
        if (FORM.hint) head.appendChild(el('span', 'staff-form-hint', FORM.hint));
        box.appendChild(head);

        var grid = el('div', 'staff-form-grid');
        FORM.fields.forEach(function (f) {
            var wrap = el('div', 'staff-field');
            wrap.appendChild(el('label', 'staff-label', f.label + (f.required ? ' *' : '')));
            var input;
            if (f.type === 'select') {
                input = document.createElement('select');
                (f.options || []).forEach(function (o) {
                    var op = document.createElement('option');
                    op.value = o.value;
                    op.textContent = o.text;
                    input.appendChild(op);
                });
            } else if (f.type === 'date') {
                input = document.createElement('input');
                input.type = 'date';
                input.max = todayStr();
            } else {
                input = document.createElement('input');
                input.type = 'text';
                if (f.options && f.options.length) {
                    var dl = document.createElement('datalist');
                    dl.id = 'ff_list_' + f.key;
                    f.options.forEach(function (o) {
                        var op = document.createElement('option');
                        op.value = o.value;
                        dl.appendChild(op);
                    });
                    wrap.appendChild(dl);
                    input.setAttribute('list', dl.id);
                }
            }
            input.id = 'ff_' + f.key;
            input.className = 'staff-input-el';
            if (f.placeholder) input.placeholder = f.placeholder;
            wrap.appendChild(input);
            if (f.hint) wrap.appendChild(el('div', 'staff-fhint', f.hint));
            grid.appendChild(wrap);
        });
        box.appendChild(grid);

        var actions = el('div', 'staff-form-actions');
        var submit = el('button', 'staff-btn primary', FORM.submit || '提交');
        submit.type = 'button';
        submit.addEventListener('click', submitForm);
        actions.appendChild(submit);
        box.appendChild(actions);
    }

    function readForm() {
        var values = {}, missing = '';
        (FORM.fields || []).forEach(function (f) {
            var n = document.getElementById('ff_' + f.key);
            if (!n) return;
            var v = (n.value || '').trim();
            if (f.required && !v) { missing = missing || f.label; return; }
            if (v) values[f.key] = v;
        });
        return { values: values, missing: missing };
    }

    function flashForm(msg) {
        var old = document.getElementById('ffErr');
        if (old && old.parentNode) old.parentNode.removeChild(old);
        var n = el('div', 'staff-form-err', msg);
        n.id = 'ffErr';
        var box = document.getElementById('staffForm');
        if (box) box.appendChild(n);
    }

    function submitForm() {
        if (es) return;
        var r = readForm();
        if (r.missing) { flashForm('请先填写「' + r.missing + '」'); return; }
        if (!r.values.birth) { flashForm('请先填写出生日期'); return; }
        var p = r.values.birth.split('-');
        var fields = {
            year: parseInt(p[0], 10),
            month: parseInt(p[1], 10),
            day: parseInt(p[2], 10),
            hour: r.values.hour || '未知',
            gender: r.values.gender || '未知',
            city: r.values.city || ''
        };
        if (!fields.year || !fields.month || !fields.day) { flashForm('出生日期不完整'); return; }
        var whose = r.values.name ? r.values.name : '这个人';
        startRequest('请基于我填写的出生信息，分析' + whose +
            '的性格特点、适合的工作方向，以及和他协作时要注意什么。', fields);
    }

    function setBusy(busy) {
        sendBtn.disabled = busy;
        stopBtn.disabled = !busy;
        hint.textContent = busy ? '正在取数，可随时点「停止」' : 'Ctrl+Enter 发送';
    }

    function closeStream() {
        if (es) { try { es.close(); } catch (e) { } }
        es = null;
        setBusy(false);
    }

    function startRequest(text, fields) {
        addUser(text);
        setBusy(true);

        var body = { message: text, staff: STAFF_ID };
        if (fields) body.fields = fields;
        fetch('/staff/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (!res || res.code !== 200 || !res.data || !res.data.task_id) {
                    setBusy(false);
                    var slot = addAssistant();
                    finishAnswer(slot, '提问失败：' + ((res && res.msg) || '未知错误'));
                    return;
                }
                taskId = res.data.task_id;
                var slot = addAssistant();
                var gotFinal = false;
                es = new EventSource('/staff/api/stream/' + taskId);
                es.onmessage = function (e) {
                    var ev;
                    try { ev = JSON.parse(e.data); } catch (err) { return; }
                    if (ev.type === 'step') {
                        addStep(slot.steps, ev.text);
                    } else if (ev.type === 'tool') {
                        addTool(slot.steps, ev);
                    } else if (ev.type === 'final') {
                        gotFinal = true;
                        finishAnswer(slot, ev.text || '（模型没有给出答案）');
                    } else if (ev.type === 'error') {
                        addStep(slot.steps, '出错：' + (ev.msg || ''));
                    } else if (ev.type === 'timeout') {
                        gotFinal = true;
                        finishAnswer(slot, '任务超时已中断，请缩小问题范围再试。');
                    } else if (ev.type === 'done') {
                        if (!gotFinal) {
                            finishAnswer(slot, '（未取得答案：连接中断或模型未响应，请重试）');
                        }
                        closeStream();
                    }
                };
                es.onerror = function () {
                    closeStream();
                    if (!gotFinal) {
                        finishAnswer(slot, '（连接中断，请重试）');
                    }
                };
            })
            .catch(function (err) {
                setBusy(false);
                var slot = addAssistant();
                finishAnswer(slot, '请求失败：' + err);
            });
    }

    function send() {
        var text = (ta.value || '').trim();
        if (!text || es) return;
        ta.value = '';
        startRequest(text, null);
    }

    sendBtn.addEventListener('click', send);
    stopBtn.addEventListener('click', function () {
        closeStream();
        if (taskId) {
            fetch('/staff/api/cancel/' + taskId, { method: 'POST' }).catch(function () { });
        }
    });
    ta.addEventListener('keydown', function (e) {
        if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); send(); }
    });

    // 能力开关展示
    fetch('/staff/api/state')
        .then(function (r) { return r.json(); })
        .then(function (res) {
            var d = (res && res.data) || {};
            var items = [];
            items.push(['自由查库', !!d.allow_sql]);
            items.push(['身份', d.is_admin ? '管理员' : '普通用户']);
            items.push(['AI 后端', d.backend || '-']);
            (flags ? flags : document.body).innerHTML = '';
            items.forEach(function (it) {
                var f = el('span', 'staff-flag ' + (it[1] ? 'on' : 'off'), it[0] + '：' + it[1]);
                flags.appendChild(f);
            });
        })
        .catch(function () { });

    buildForm();
    emptyState();
    setBusy(false);
})();
