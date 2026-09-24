/*!
 * AI 回答排版渲染器 —— Markdown + 代码高亮 + XSS 过滤
 *
 * 依赖（本地化，离线可用）：static/vendor/ai-md/{marked,highlight,purify}.min.js
 * 对外接口：
 *   window.AiMarkdown.render(text)      -> 安全 HTML 字符串
 *   window.AiMarkdown.renderInto(el, s) -> 直接写入元素
 *
 * 设计原则：
 *   1) 只改变「呈现方式」，绝不增删改任何原文字符（同一段文字，结构化呈现）。
 *   2) 先 DOMPurify 过滤、再 hljs 上色（顺序反了高亮 span 会被 sanitize 抹掉）。
 *   3) 复制按钮走 document 事件委托，因为最终落地是 innerHTML 字符串，
 *      内联 onclick 无法存活。
 *   4) 表格 / pre 外层自动包滚动容器 —— AI 回答区在侧栏很窄，长表格会撑破布局。
 */
(function (window, document) {
    'use strict';

    // 语言标识 -> 展示名（识别不出来时退化为原始标识大写 / CODE）
    var LANG_LABELS = {
        python: 'Python', py: 'Python', javascript: 'JavaScript', js: 'JavaScript',
        typescript: 'TypeScript', ts: 'TypeScript', bash: 'Bash', sh: 'Shell',
        shell: 'Shell', zsh: 'Shell', sql: 'SQL', json: 'JSON', yaml: 'YAML',
        yml: 'YAML', html: 'HTML', xml: 'XML', css: 'CSS', scss: 'SCSS',
        less: 'Less', java: 'Java', cpp: 'C++', 'c++': 'C++', c: 'C',
        csharp: 'C#', 'c#': 'C#', cs: 'C#', go: 'Go', rust: 'Rust', php: 'PHP',
        ruby: 'Ruby', kotlin: 'Kotlin', swift: 'Swift', scala: 'Scala',
        markdown: 'Markdown', md: 'Markdown', diff: 'Diff', ini: 'INI',
        toml: 'TOML', plaintext: 'Text', text: 'Text', makefile: 'Makefile',
        dockerfile: 'Dockerfile', r: 'R', lua: 'Lua', perl: 'Perl'
    };

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // 兜底渲染：库缺失或解析异常时，退回「转义 + 换行保持」，行为与改造前一致
    function plainFallback(text) {
        return '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>';
    }

    // 已知语言名（按长度倒序）用于「围栏语言与首行代码粘连」的还原
    var FENCE_LANG_ALT = Object.keys(LANG_LABELS)
        .sort(function (a, b) { return b.length - a.length; })
        .map(function (k) { return k.replace(/[+#.]/g, '\\$&'); })
        .join('|');

    /**
     * 防御性修复：把上游/模型偶发造成的「坏结构」还原成可解析形状。
     * 只补回结构性换行，不增删改任何正文字符。
     *
     * 背景：上游流式清洗在「开头缓冲」处曾吞掉紧邻的第一个换行，
     * 由此产生两个症状：
     *   ① ```python + 首行代码粘连 → 语言标签变成 PYTHONSCORE、hljs 找不到语言 → 不高亮；
     *   ② 表格「表头行 | 分隔行」粘连  → marked 不识别表格 → 原样裸奔。
     * 上游已修；此处再兜一层，兼容模型自身偶发的同类粘连。
     */
    function repairMarkdown(text) {
        var s = String(text == null ? '' : text);

        // 1) 统一换行：\r\n / \r → \n（残留 CR 会让 marked 的表格/围栏判定失效）
        s = s.replace(/\r\n?/g, '\n');

        // 2) 围栏语言与首行代码粘连（已知语言，中间无空格）：
        //    ```pythonscore = 1  →  ```python⏎score = 1
        //    ⚠️ 必须先短路「正常围栏行」：交替匹配失败后会回溯到更短的语言（python → py），
        //    把「```python」拆成「```py⏎thon」，正文凭空多出 "thon"。
        s = s.split('\n').map(function (line) {
            if (/^[ \t]*`{3,}[ \t]*[A-Za-z0-9_+#.-]*[ \t]*$/.test(line)) return line;   // 纯围栏行，原样保留
            return line.replace(new RegExp('^([ \\t]*`{3,}[ \\t]*)(' + FENCE_LANG_ALT + ')(\\S)'), '$1$2\n$3');
        }).join('\n');

        // 2b) 围栏语言位不是已知语言、且行内还有别的内容 → 整行其实是首行代码：
        //     ```score = 1  →  ```⏎score = 1
        s = s.replace(/^([ \t]*)(`{3,})[ \t]*([A-Za-z][A-Za-z0-9_+#.-]*)[ \t]+(\S[^\n]*)$/gm,
            function (m, ind, fence, tok, rest) {
                if (LANG_LABELS[tok.toLowerCase()]) return m;      // 已知语言：保留为信息串
                return ind + fence + '\n' + tok + ' ' + rest;
            });

        // 3) 表格「表头行 / 分隔行」粘连：| 示例 || --- | --- |  → 两个 | 之间补换行。
        //    只处理以 | 开头的行；分隔行以 -{3,} 为准（空单元格 |  | 与正文里的 || 都不会命中）。
        s = s.split('\n').map(function (line) {
            if (line.replace(/^[ \t]+/, '').charAt(0) !== '|') return line;
            return line.replace(/\|(?=[ \t]*\|[ \t]*:?-{3,}[ \t:]*(?:\||$))/g, '|\n');
        }).join('\n');

        return s;
    }

    // 语言位 hljs 不认识时（如被粘连污染成 pythonscore），标记会显示成乱码、且 hljs 直接
    // 放弃高亮。这里去掉该 class，交回 hljs 自动识别 —— 至少保证「有高亮 + 标签正常」。
    function fixUnknownLangs(root) {
        if (typeof hljs === 'undefined' || !hljs.getLanguage) return;
        var codes = root.querySelectorAll('pre > code');
        Array.prototype.forEach.call(codes, function (el) {
            var m = String(el.className || '').match(/language-([\w+#.-]+)/i);
            if (!m || el.classList.contains('hljs')) return;
            try {
                if (!hljs.getLanguage(m[1])) el.className = '';
            } catch (e) { /* 忽略 */ }
        });
    }

    function toHtml(text) {
        if (typeof marked === 'undefined') return plainFallback(text);
        try {
            // gfm: 支持表格；breaks: 单换行视为 <br>（与原 replace(\n,'<br>') 行为对齐）
            marked.setOptions({ gfm: true, breaks: true });
            return marked.parse(String(text == null ? '' : text));
        } catch (e) {
            return plainFallback(text);
        }
    }

    function sanitize(html) {
        if (typeof DOMPurify === 'undefined') return html;
        try {
            return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
        } catch (e) {
            return html;
        }
    }

    function highlightIn(root) {
        if (typeof hljs === 'undefined') return;
        var codes = root.querySelectorAll('pre > code');
        Array.prototype.forEach.call(codes, function (el) {
            try { hljs.highlightElement(el); } catch (e) { /* 高亮失败不影响正文 */ }
        });
    }

    // 给每个 <pre> 套一层带「语言标签 + 复制按钮」的面板
    function decorateCode(root) {
        var pres = root.querySelectorAll('pre');
        Array.prototype.forEach.call(pres, function (pre) {
            if (!pre.parentNode) return;
            if (pre.parentNode.classList && pre.parentNode.classList.contains('ai-md-code')) return;

            var codeEl = pre.querySelector('code');
            var lang = '';
            if (codeEl) {
                var m = String(codeEl.className || '').match(/language-([\w+#.-]+)/i);
                if (m) lang = m[1].toLowerCase();
            }

            var wrap = document.createElement('div');
            wrap.className = 'ai-md-code';

            var head = document.createElement('div');
            head.className = 'ai-md-code-head';

            var tag = document.createElement('span');
            tag.className = 'ai-md-code-lang';
            tag.textContent = LANG_LABELS[lang] || (lang ? lang.toUpperCase() : 'CODE');

            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'ai-md-copy';
            btn.textContent = '复制';

            head.appendChild(tag);
            head.appendChild(btn);

            pre.parentNode.insertBefore(wrap, pre);
            wrap.appendChild(head);
            wrap.appendChild(pre);
        });
    }

    function wrapTables(root) {
        var tables = root.querySelectorAll('table');
        Array.prototype.forEach.call(tables, function (t) {
            if (!t.parentNode) return;
            if (t.parentNode.classList && t.parentNode.classList.contains('ai-md-table-wrap')) return;
            var wrap = document.createElement('div');
            wrap.className = 'ai-md-table-wrap';
            t.parentNode.insertBefore(wrap, t);
            wrap.appendChild(t);
        });
    }

    function fixLinks(root) {
        var links = root.querySelectorAll('a[href]');
        Array.prototype.forEach.call(links, function (a) {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer');
        });
    }

    function render(text) {
        if (text == null || String(text).trim() === '') return '';

        var holder = document.createElement('div');
        holder.innerHTML = sanitize(toHtml(repairMarkdown(text)));

        fixUnknownLangs(holder);  // 未知语言位先清掉，让 hljs 能自动识别
        highlightIn(holder);      // 先高亮
        wrapTables(holder);       // 再包滚动容器
        decorateCode(holder);     // 最后套代码面板
        fixLinks(holder);

        return holder.innerHTML;
    }

    function renderInto(el, text) {
        if (!el) return el;
        el.innerHTML = render(text);
        return el;
    }

    // ---------- 复制按钮：事件委托 ----------
    function fallbackCopy(text, cb) {
        try {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.top = '-1000px';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            cb();
        } catch (e) { /* 复制失败不提示，静默 */ }
    }

    function onDocClick(e) {
        var target = e.target;
        if (!target || !target.closest) return;
        var btn = target.closest('.ai-md-copy');
        if (!btn) return;
        var box = btn.closest('.ai-md-code');
        if (!box) return;
        var codeEl = box.querySelector('pre > code');
        if (!codeEl) return;

        var text = codeEl.textContent || '';
        var done = function () {
            btn.textContent = '已复制';
            btn.classList.add('is-copied');
            setTimeout(function () {
                btn.textContent = '复制';
                btn.classList.remove('is-copied');
            }, 1600);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
        } else {
            fallbackCopy(text, done);
        }
    }

    document.addEventListener('click', onDocClick);

    /**
     * 把答案里的 [n] 引用角标包成 <span class="cite-ref" data-cite="n">。
     * 只在【文本节点】上做替换——直接对整段 HTML 字符串做 replace 会误伤
     * 标签属性，也会把代码块里的 [1] 变成角标。
     * 首页 chatBox 上已有针对 .cite-ref 的事件委托（点击滚动到来源卡片），
     * 这里沿用同样的 class / data-cite 即可无缝衔接。
     */
    function highlightCitations(root) {
        if (!root) return root;
        var re = /\[(\d+)\]/g;
        var nodes = [];
        var walker = document.createTreeWalker(root, window.NodeFilter.SHOW_TEXT, null);
        var n;
        while ((n = walker.nextNode())) {
            if (!n.nodeValue || n.nodeValue.indexOf('[') < 0) continue;
            var p = n.parentNode;
            if (p && p.closest && p.closest('pre, code, .ai-md-code')) continue;
            nodes.push(n);
        }
        nodes.forEach(function (node) {
            var text = node.nodeValue;
            re.lastIndex = 0;
            if (!re.test(text)) return;
            re.lastIndex = 0;
            var frag = document.createDocumentFragment();
            var last = 0, m;
            while ((m = re.exec(text))) {
                if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
                var span = document.createElement('span');
                span.className = 'cite-ref';
                span.setAttribute('data-cite', m[1]);
                span.textContent = m[0];
                frag.appendChild(span);
                last = m.index + m[0].length;
            }
            if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
            if (node.parentNode) node.parentNode.replaceChild(frag, node);
        });
        return root;
    }

    window.AiMarkdown = {
        render: render,
        renderInto: renderInto,
        escapeHtml: escapeHtml,
        highlightCitations: highlightCitations
    };
})(window, document);
