(function() {
    const themeToggle = document.getElementById('themeToggle');
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    const navItems = document.querySelectorAll('.nav-item');
    const railSegs = document.querySelectorAll('.light-rail-seg');
    const railName = document.getElementById('lightRailName');

    const THEMES = ['quantum', 'aurora', 'plasma', 'magma', 'silver'];
    const THEME_LABELS = {
        quantum: '量子青', aurora: '极光紫', plasma: '等离子绿',
        magma: '熔岩橙', silver: '镭射银'
    };

    // ---- 默认值 ----
    const DEFAULT_MODE = 'light';
    const DAY_DEFAULT_THEME = 'quantum';
    const NIGHT_DEFAULT_THEME = 'quantum';

    // ---- localStorage keys ----
    const KEY_MODE = 'scitechhub_theme';
    const KEY_LIGHT_THEME_DAY = 'scitechhub_light_theme_day';
    const KEY_LIGHT_THEME_NIGHT = 'scitechhub_light_theme_night';
    const KEY_OLD_LIGHT_THEME = 'scitechhub_light_theme';

    // ---- 状态 ----
    let currentMode = DEFAULT_MODE;
    let dayLightTheme = DAY_DEFAULT_THEME;
    let nightLightTheme = NIGHT_DEFAULT_THEME;

    // ---- 工具：读写 localStorage ----
    function getStorage(key, fallback) {
        try {
            const val = localStorage.getItem(key);
            return val !== null ? val : fallback;
        } catch (e) { return fallback; }
    }
    function setStorage(key, val) {
        try { localStorage.setItem(key, val); } catch (e) {}
    }

    // ---- 获取当前模式对应的灯光主题 ----
    function getThemeForMode(mode) {
        return mode === 'dark' ? nightLightTheme : dayLightTheme;
    }

    // ---- 应用灯光主题（UI + data属性） ----
    function applyLightTheme(themeName) {
        if (!THEMES.includes(themeName)) themeName = DAY_DEFAULT_THEME;
        document.body.setAttribute('data-light-theme', themeName);
        if (railSegs && railSegs.length) {
            railSegs.forEach(seg => {
                const isActive = seg.dataset.theme === themeName;
                seg.classList.toggle('active', isActive);
                seg.setAttribute('aria-checked', isActive ? 'true' : 'false');
            });
        }
        if (railName) railName.textContent = THEME_LABELS[themeName] || themeName;
    }

    // ---- 应用明暗模式 ----
    function applyMode(mode) {
        const isDark = mode === 'dark';
        document.body.classList.toggle('dark', isDark);
        if (themeToggle) {
            themeToggle.classList.toggle('light', !isDark);
            themeToggle.classList.toggle('dark', isDark);
            themeToggle.setAttribute('aria-checked', isDark ? 'true' : 'false');
        }
        currentMode = mode;

        const theme = getThemeForMode(mode);
        applyLightTheme(theme);
    }

    // ---- 切换明暗模式 ----
    function toggleMode() {
        const newMode = currentMode === 'light' ? 'dark' : 'light';
        applyMode(newMode);
        setStorage(KEY_MODE, newMode);
        triggerTogglePulse();
    }

    // ---- 切换灯光主题 ----
    function selectLightTheme(themeName) {
        if (!THEMES.includes(themeName)) return;
        if (currentMode === 'dark') {
            nightLightTheme = themeName;
            setStorage(KEY_LIGHT_THEME_NIGHT, themeName);
        } else {
            dayLightTheme = themeName;
            setStorage(KEY_LIGHT_THEME_DAY, themeName);
        }
        applyLightTheme(themeName);
    }

    // ---- 脉冲动画 ----
    function triggerTogglePulse() {
        if (!themeToggle) return;
        themeToggle.classList.remove('pulse');
        void themeToggle.offsetWidth;
        themeToggle.classList.add('pulse');
        const onEnd = function(e) {
            if (e.animationName === 'warm-pulse' || e.animationName === 'avatar-pulse') {
                themeToggle.classList.remove('pulse');
                themeToggle.removeEventListener('animationend', onEnd);
            }
        };
        themeToggle.addEventListener('animationend', onEnd);
    }

    // ---- 侧边栏折叠 ----
    function setSidebarState(collapsed) {
        if (!sidebar) return;
        sidebar.classList.toggle('collapsed', collapsed);
        try { localStorage.setItem('scitechhub_sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (e) {}
    }
    function getSidebarState() {
        return sidebar ? sidebar.classList.contains('collapsed') : false;
    }
    function handleSidebarToggle() {
        setSidebarState(!getSidebarState());
    }

    // ---- 页面切换（高亮当前导航） ----
    function switchPage(moduleId) {
        const items = document.querySelectorAll('.nav-item[data-module]');
        items.forEach(item => {
            const isActive = item.dataset.module === moduleId;
            item.classList.toggle('active', isActive);
            item.setAttribute('aria-current', isActive ? 'page' : 'false');
        });
        try { localStorage.setItem('scitechhub_page', moduleId); } catch (e) {}
    }

    function handleKeyboard(handler) {
        return function(e) {
            if (e.type === 'keydown' && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                handler.call(this, e);
            }
        };
    }

    // ---- 用户菜单切换（支持多个菜单实例：侧栏 / 库模块右侧 user-bar） ----
    function _allUserMenus() {
        return Array.from(document.querySelectorAll('.user-menu'));
    }
    function _closeAllMenus(exceptId) {
        _allUserMenus().forEach(function(m) {
            if (!exceptId || m.id !== exceptId) m.classList.remove('show');
        });
    }
    function toggleUserMenu(evt, menuId) {
        if (evt) { evt.stopPropagation(); }
        // 默认兼容：未指定 menuId 时取 #userMenu 或第一个
        const targetId = menuId || 'userMenu';
        let menu = document.getElementById(targetId);
        if (!menu) {
            const all = _allUserMenus();
            menu = all.length ? all[0] : null;
        }
        if (!menu) return;
        // 切换自身；切换时关闭其它实例
        const willOpen = !menu.classList.contains('show');
        _closeAllMenus(menu.id);
        if (willOpen) {
            _portalMenu(menu);        // 关键：portal 到 body 脱离 backdrop-filter 造成的 containing block
            menu.classList.add('show');       // 先显示，才能量到真实宽高
            _positionMenu(menu);      // 再根据 trigger 位置固定 top/right（用实测尺寸翻转判断）
        } else {
            menu.classList.remove('show');
        }
    }

    // ---- Portal 模式：把 .user-menu 移到 document.body ----
    // 之所以必须 portal：.container-wrap 和 .sidebar 上都用了 backdrop-filter，
    // 它会建立新的 containing block，让 position:fixed 的元素以 sidebar 为坐标原点，
    // 而不是 viewport。移到 body 后，body 没有 backdrop-filter/filter/transform，
    // fixed 坐标就真正基于视口了。
    function _portalMenu(menu) {
        if (!menu) return;
        if (menu.parentElement !== document.body) {
            document.body.appendChild(menu);
        }
    }

    // ---- 菜单位置：根据 trigger 元素动态设置 .user-menu 的 fixed top/right ----
    // 由于 .user-menu 在 CSS 中改为 position:fixed（脱离任何父级 overflow 限制），
    // 且 portal 到 body（避免 backdrop-filter 形成新的 containing block），
    // 此时 fixed 才是真正基于视口的；这里根据 trigger 的 getBoundingClientRect() 设置 top/right。
    function _positionMenu(menu) {
        if (!menu) return;
        // menu 已经 portal 到 body，触发器不再在同一棵 DOM 上 —— 全局查找
        let trigger = null;
        // 当前只支持 side / book lib 两个实例，按 menu id 区分查找范围
        if (menu.id === 'userMenuSidebar') {
            trigger = document.querySelector('.header-user-trigger');
        } else if (menu.id === 'userMenuBookLib') {
            trigger = document.querySelector('[data-user-bar-trigger="userMenuBookLib"]');
        } else {
            trigger = document.querySelector('.header-user-trigger')
                   || document.querySelector('[data-user-bar-trigger]');
        }
        if (!trigger || !trigger.getBoundingClientRect) return;
        const rect = trigger.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const margin = 6;
        // 菜单已经 display:block（先 show 再 position），直接量实测尺寸；
        // 量不到时兜底一个粗估值（菜单内容项数不定，不能写死）
        const estMenuH = menu.offsetHeight || 180;
        const estMenuW = menu.offsetWidth || 200;
        // 默认：菜单右下角对齐 trigger，右边缘 = vw - trigger.right
        let right = vw - rect.right;
        if (right < margin) right = margin;
        if (right > vw - estMenuW - margin) right = vw - estMenuW - margin;
        // 默认：菜单顶部 = trigger.bottom + margin
        let top = rect.bottom + margin;
        // 如果下方空间不够，翻转到上方
        if (top + estMenuH > vh - margin) {
            top = rect.top - margin - estMenuH;
            if (top < margin) top = margin;
        }
        menu.style.top = top + 'px';
        menu.style.right = right + 'px';
        menu.style.left = 'auto';
        menu.style.bottom = 'auto';
    }
    document.addEventListener('click', function(e) {
        const menus = _allUserMenus();
        if (!menus.length) return;
        // 点击任意 .user-bar / .header-user / .user-menu 内部不关闭；其它地方点击关闭全部
        const inside = e.target.closest('.user-menu, .header-user, .user-bar');
        if (!inside) _closeAllMenus();
    });

    // ---- 个人中心弹窗：修改密码 / 修改名字（迁移自原登录头像菜单） ----
    function closeUserMenu() { _closeAllMenus(); }
    function openModal(id) {
        const m = document.getElementById(id);
        if (m) m.classList.add('show');
    }
    function closeModal(id) {
        const m = document.getElementById(id);
        if (m) m.classList.remove('show');
    }
    function setMsg(elId, text, ok) {
        const el = document.getElementById(elId);
        if (!el) return;
        el.textContent = text || '';
        el.classList.toggle('ok', !!ok);
    }
    function openChangePwd() {
        closeUserMenu();
        openModal('maskPwd');
    }
    function closeChangePwd() {
        closeModal('maskPwd');
        ['oldPwd', 'newPwd', 'confirmPwd'].forEach(function(id) {
            const el = document.getElementById(id); if (el) el.value = '';
        });
        setMsg('pwdMsg', '', false);
    }
    function openChangeName() {
        closeUserMenu();
        const input = document.getElementById('newName');
        if (input) setTimeout(function() { input.focus(); }, 50);
        openModal('maskName');
    }
    function closeChangeName() {
        closeModal('maskName');
        const el = document.getElementById('newName'); if (el) el.value = '';
        setMsg('nameMsg', '', false);
    }
    function submitChangePwd() {
        const oldPwd = (document.getElementById('oldPwd').value || '').trim();
        const newPwd = document.getElementById('newPwd').value;
        const confirm = document.getElementById('confirmPwd').value;
        if (!oldPwd || !newPwd || !confirm) { setMsg('pwdMsg', '请填写完整信息'); return; }
        if (newPwd !== confirm) { setMsg('pwdMsg', '两次输入的新密码不一致'); return; }
        if (newPwd.length < 6) { setMsg('pwdMsg', '新密码至少 6 位'); return; }
        const fd = new FormData();
        fd.append('old_pwd', oldPwd);
        fd.append('new_pwd', newPwd);
        fd.append('confirm_pwd', confirm);
        fetch('/api/account/change-password', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: fd
        })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res.code === 200) { setMsg('pwdMsg', '密码修改成功', true); setTimeout(closeChangePwd, 800); }
            else setMsg('pwdMsg', res.msg || '修改失败');
        })
        .catch(function() { setMsg('pwdMsg', '网络错误，请重试'); });
    }
    function submitChangeName() {
        const input = document.getElementById('newName');
        const name = (input.value || '').trim();
        if (!name) { setMsg('nameMsg', '名字不能为空'); return; }
        const fd = new FormData();
        fd.append('name', name);
        fetch('/api/account/change-name', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: fd
        })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res.code === 200) { setMsg('nameMsg', '名字修改成功', true); setTimeout(function() { location.reload(); }, 800); }
            else setMsg('nameMsg', res.msg || '修改失败');
        })
        .catch(function() { setMsg('nameMsg', '网络错误，请重试'); });
    }

    // ---- 兼容性迁移 ----
    function migrateOldTheme() {
        try {
            const oldTheme = localStorage.getItem(KEY_OLD_LIGHT_THEME);
            if (oldTheme && THEMES.includes(oldTheme)) {
                const hasDay = localStorage.getItem(KEY_LIGHT_THEME_DAY) !== null;
                const hasNight = localStorage.getItem(KEY_LIGHT_THEME_NIGHT) !== null;
                if (!hasDay) {
                    localStorage.setItem(KEY_LIGHT_THEME_DAY, oldTheme);
                }
                if (!hasNight) {
                    if (oldTheme === 'silver') {
                        localStorage.setItem(KEY_LIGHT_THEME_NIGHT, 'silver');
                    } else {
                        localStorage.setItem(KEY_LIGHT_THEME_NIGHT, NIGHT_DEFAULT_THEME);
                    }
                }
            }
        } catch (e) {}
    }

    // ---- 初始化 ----
    function init() {
        migrateOldTheme();

        let savedMode = DEFAULT_MODE;
        try {
            const m = localStorage.getItem(KEY_MODE);
            if (m === 'light' || m === 'dark') savedMode = m;
        } catch (e) {}

        let savedDay = getStorage(KEY_LIGHT_THEME_DAY, DAY_DEFAULT_THEME);
        let savedNight = getStorage(KEY_LIGHT_THEME_NIGHT, NIGHT_DEFAULT_THEME);
        if (!THEMES.includes(savedDay)) savedDay = DAY_DEFAULT_THEME;
        if (!THEMES.includes(savedNight)) savedNight = NIGHT_DEFAULT_THEME;

        dayLightTheme = savedDay;
        nightLightTheme = savedNight;

        let savedSidebar = 'expanded';
        try {
            const ss = localStorage.getItem('scitechhub_sidebar');
            if (ss === 'collapsed' || ss === 'expanded') savedSidebar = ss;
        } catch (e) {}

        applyMode(savedMode);
        if (sidebar) setSidebarState(savedSidebar === 'collapsed');

        if (themeToggle) {
            themeToggle.addEventListener('click', toggleMode);
            themeToggle.addEventListener('keydown', handleKeyboard(toggleMode));
        }

        if (railSegs && railSegs.length) {
            railSegs.forEach(seg => {
                const pick = function() {
                    const theme = this.dataset.theme;
                    if (theme && THEMES.includes(theme)) {
                        selectLightTheme(theme);
                    }
                };
                seg.addEventListener('click', pick);
                seg.addEventListener('keydown', handleKeyboard(pick));
            });
        }

        if (sidebarToggle) {
            sidebarToggle.addEventListener('click', handleSidebarToggle);
            sidebarToggle.addEventListener('keydown', handleKeyboard(handleSidebarToggle));
        }

        // 用户面板点击（侧边栏老触发器）
        const userPanelInner = document.querySelector('.user-panel-inner');
        if (userPanelInner) {
            userPanelInner.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleUserMenu();
            });
        }
        // LOGO 旁新触发器（header-user）
        const headerUserTrigger = document.querySelector('.header-user-trigger');
        if (headerUserTrigger) {
            headerUserTrigger.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleUserMenu(e, 'userMenuSidebar');
            });
            headerUserTrigger.addEventListener('keydown', handleKeyboard(function() { toggleUserMenu(null, 'userMenuSidebar'); }));
        }

        // 库模块右侧 user-bar 触发器（任意 [data-user-bar-trigger] 元素都触发其 data 指定的菜单）
        document.querySelectorAll('[data-user-bar-trigger]').forEach(function(trig) {
            const menuId = trig.getAttribute('data-user-bar-trigger');
            trig.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleUserMenu(e, menuId);
            });
        });

        // 个人中心弹窗：点击遮罩 / 按 ESC 关闭
        document.querySelectorAll('.app-modal-mask').forEach(function(mask) {
            mask.addEventListener('click', function(e) {
                if (e.target === mask) {
                    if (mask.id === 'maskPwd') closeChangePwd();
                    else if (mask.id === 'maskName') closeChangeName();
                }
            });
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') { closeChangePwd(); closeChangeName(); }
        });

        // 确保灯光主题名称显示正确
        if (railName) {
            const currentTheme = getThemeForMode(currentMode);
            railName.textContent = THEME_LABELS[currentTheme] || currentTheme;
        }

        // 自动高亮当前模块（从body data-current-module获取）
        const currentModule = document.body.dataset.currentModule;
        if (currentModule) {
            switchPage(currentModule);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 暴露给模块页面使用
    window.App = {
        switchPage: switchPage,
        setSidebarState: setSidebarState,
        applyMode: applyMode,
        selectLightTheme: selectLightTheme,
    };

    // ---- 暴露个人中心弹窗函数到全局，模板里的 onclick="openChangePwd()" 才能找到 ----
    // （IIFE 默认不暴露，inline onclick 走 window 域，必须显式挂上来）
    window.openChangePwd = openChangePwd;
    window.closeChangePwd = closeChangePwd;
    window.submitChangePwd = submitChangePwd;
    window.openChangeName = openChangeName;
    window.closeChangeName = closeChangeName;
    window.submitChangeName = submitChangeName;
    window.toggleUserMenu = toggleUserMenu;
    window.openModal = openModal;
    window.closeModal = closeModal;
})();
