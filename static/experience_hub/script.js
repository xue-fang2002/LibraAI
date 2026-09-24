// 经验社区 - 前端交互逻辑
(function() {
    const CONFIG = window.EXPERIENCE_HUB || {};
    let currentPage = 1;
    let isLoading = false;
    let hasMore = true;
    let currentCategory = '';
    let currentTag = '';

    // 加载帖子列表
    async function loadPosts(page = 1, append = false) {
        if (isLoading) return;
        isLoading = true;
        try {
            const url = `${CONFIG.listApi}?page=${page}&category=${currentCategory}&tag=${currentTag}`;
            const res = await fetch(url);
            const json = await res.json();
            if (json.code !== 200) {
                console.error('加载失败', json.msg);
                return;
            }
            const data = json.data;
            const posts = data.posts || [];
            hasMore = posts.length >= data.per_page;

            // 更新统计
            document.getElementById('statTotal').innerText = data.total || 0;

            const container = document.getElementById('postsContainer');
            if (!append) container.innerHTML = '';

            if (posts.length === 0 && !append) {
                container.innerHTML = '<div class="paper-card" style="grid-column:1/-1;text-align:center;padding:40px;">🌱 还没有经验，快来发布第一条吧！</div>';
                return;
            }

            posts.forEach(p => {
                const card = document.createElement('div');
                card.className = 'paper-card post-card';
                card.innerHTML = `
                    <a href="/experience/${p.id}" style="text-decoration:none;color:inherit;">
                        <div class="card-title">${p.title}</div>
                        <div class="card-excerpt">${(p.content || '').substring(0, 100)}...</div>
                        <div class="card-footer">
                            <span class="card-tag">${p.category}</span>
                            ${p.is_public === 0 ? '<span class="vis-badge vis-badge-private">🔒 私有</span>' : ''}
                            <div class="card-stats">
                                <span>❤️ ${p.likes}</span>
                                <span>⭐ ${p.collects}</span>
                                <span>👤 ${p.author_nickname || p.author_account}</span>
                            </div>
                        </div>
                    </a>
                `;
                container.appendChild(card);
            });

            if (!hasMore) {
                document.getElementById('loadMoreBtn').style.display = 'none';
            } else {
                document.getElementById('loadMoreBtn').style.display = 'inline-block';
            }
        } catch (e) {
            console.error(e);
        } finally {
            isLoading = false;
        }
    }

    // 加载热门标签
    async function loadHotTags() {
        try {
            const res = await fetch(CONFIG.hotTagsApi);
            const json = await res.json();
            if (json.code === 200) {
                const tags = json.data || [];
                const cloud = document.getElementById('tagCloud');
                cloud.innerHTML = tags.map(t =>
                    `<span onclick="window.EXPERIENCE_HUB.searchTag('${t.name}')">${t.name} (${t.count})</span>`
                ).join('');
            }
        } catch (e) { console.error(e); }
    }

    // 加载热榜（从已有数据中取前5）
    async function loadHotList() {
        try {
            const res = await fetch(`${CONFIG.listApi}?page=1&per_page=5`);
            const json = await res.json();
            if (json.code === 200) {
                const posts = json.data.posts || [];
                const list = document.getElementById('hotList');
                if (posts.length === 0) {
                    list.innerHTML = '<li>暂无热门</li>';
                    return;
                }
                list.innerHTML = posts.map((p, i) =>
                    `<li>${i+1}. <a href="/experience/${p.id}" style="color:var(--text-dark);">${p.title}</a>` +
                    `${p.is_public === 0 ? ' <span class="vis-badge vis-badge-private">🔒</span>' : ''}` +
                    ` ⭐${p.collects}</li>`
                ).join('');
            }
        } catch (e) { console.error(e); }
    }

    // 事件绑定
    document.addEventListener('DOMContentLoaded', function() {
        // 1. 分类切换
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                currentCategory = this.dataset.cat || '';
                currentPage = 1;
                loadPosts(1, false);
            });
        });

        // 2. 标签搜索
        document.getElementById('tagSearch')?.addEventListener('keyup', function(e) {
            if (e.key === 'Enter') {
                currentTag = this.value.trim();
                currentPage = 1;
                loadPosts(1, false);
            }
        });

        // 3. 加载更多
        document.getElementById('loadMoreBtn')?.addEventListener('click', function() {
            if (hasMore && !isLoading) {
                currentPage++;
                loadPosts(currentPage, true);
            }
        });

        // 4. 全局暴露搜索标签
        window.EXPERIENCE_HUB.searchTag = function(tag) {
            document.getElementById('tagSearch').value = tag;
            currentTag = tag;
            currentPage = 1;
            loadPosts(1, false);
        };

        // 5. AI 问问社区（复用全局保守问答 /home/api/chat）
        const aiAskInput = document.getElementById('aiAskInput');
        const aiAskBtn = document.getElementById('aiAskBtn');
        const aiAskAnswer = document.getElementById('aiAskAnswer');

        // 思考阶段文案（分阶段推进，让「等待」有进度感）
        const THINK_STAGES = ['正在检索资料库…', '正在组织语言…', '正在润色答案…'];
        let stopThinking = null;

        function setThinking(on) {
            if (!aiAskBtn) return;
            aiAskBtn.disabled = !!on;
            aiAskBtn.classList.toggle('is-thinking', !!on);
        }

        // 回答框内「思考中」提示：旋转法阵 + 分阶段文案 + 呼吸点
        function startThinking() {
            aiAskAnswer.innerHTML =
                '<div class="ai-thinking">' +
                '<span class="ai-think-orb" aria-hidden="true"></span>' +
                '<span class="ai-think-text">' + THINK_STAGES[0] + '</span>' +
                '<span class="ai-think-dots" aria-hidden="true"><i></i><i></i><i></i></span>' +
                '</div>';
            const textEl = aiAskAnswer.querySelector('.ai-think-text');
            let si = 0;
            const timer = setInterval(function () {
                if (si >= THINK_STAGES.length - 1) return;
                si += 1;
                if (textEl) textEl.textContent = THINK_STAGES[si];
            }, 1500);
            return function () { clearInterval(timer); };
        }

        function sendAiAsk() {
            const q = (aiAskInput && aiAskInput.value || '').trim();
            if (!q) return;
            if (!CONFIG.currentUser) {
                aiAskAnswer.innerHTML = '<div class="ai-ask-hint">🔐 <a href="' + (CONFIG.loginUrl || '/login') + '">登录</a> 后可使用 AI 问答</div>';
                return;
            }
            setThinking(true);
            stopThinking = startThinking();
            fetch('/home/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ question: q, mode: 'conservative' })
            })
            .then(r => r.json())
            .then(res => {
                if (res && res.code === 200 && res.data) {
                    // Markdown 渲染（表格 / 代码块 / 引用 / 列表…），只改呈现、不改原文。
                    // AiMarkdown 缺失时退回旧行为，保证不会白屏。
                    const raw = res.data.answer || '';
                    const ans = window.AiMarkdown ? window.AiMarkdown.render(raw) : raw.replace(/\n/g, '<br>');
                    let src = '';
                    const srcs = res.data.sources || [];
                    if (srcs.length) {
                        const names = srcs.slice(0, 3)
                            .map(s => (s && (s.book || s.title)) || '')
                            .filter(Boolean);
                        if (names.length) src = '<div class="ai-ask-sources">📚 参考：' + names.join('、') + '</div>';
                    }
                    aiAskAnswer.innerHTML = '<div class="ai-ask-bubble ai-md">' + ans + '</div>' + src;
                } else {
                    aiAskAnswer.innerHTML = '<div class="ai-ask-hint">' + ((res && res.msg) || 'AI 暂时不可用，请稍后再试') + '</div>';
                }
            })
            .catch(() => {
                aiAskAnswer.innerHTML = '<div class="ai-ask-hint">网络错误，请稍后再试</div>';
            })
            .finally(() => {
                setThinking(false);
                if (stopThinking) { stopThinking(); stopThinking = null; }
            });
        }
        if (aiAskBtn) aiAskBtn.addEventListener('click', sendAiAsk);
        if (aiAskInput) aiAskInput.addEventListener('keyup', e => { if (e.key === 'Enter') sendAiAsk(); });

        // 6. 索引维护：重建经验索引（按钮仅管理员可见）
        const reindexBtn = document.getElementById('reindexBtn');
        const reindexMsg = document.getElementById('reindexMsg');
        if (reindexBtn && reindexMsg) {
            reindexBtn.addEventListener('click', function() {
                if (reindexBtn.disabled || !CONFIG.reindexApi) return;
                if (!confirm('将按当前模型重建全部经验帖的向量索引，可能需要一些时间。确定继续？')) return;
                reindexBtn.disabled = true;
                reindexMsg.className = 'admin-index-msg';
                reindexMsg.textContent = '🔄 正在重建，请稍候…';
                fetch(CONFIG.reindexApi, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: JSON.stringify({})
                })
                .then(r => r.json())
                .then(res => {
                    if (res && res.code === 200 && res.data) {
                        reindexMsg.textContent = '✅ ' + (res.msg || '重建完成');
                        reindexMsg.classList.add('ok');
                    } else {
                        reindexMsg.textContent = '❌ ' + ((res && res.msg) || '重建失败');
                        reindexMsg.classList.add('err');
                    }
                })
                .catch(() => {
                    reindexMsg.textContent = '❌ 网络错误，请稍后再试';
                    reindexMsg.classList.add('err');
                })
                .finally(() => { reindexBtn.disabled = false; });
            });
        }

        // 7. 初始化加载
        loadPosts(1, false);
        loadHotTags();
        loadHotList();
    });
})();