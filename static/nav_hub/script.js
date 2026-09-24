/**
 * 网站导航模块 (nav_hub) 前端逻辑
 * ================================
 * - 公开访问：所有人可查看
 * - 管理操作：需后端返回 can_manage=true 才显示编辑按钮
 */

(function() {
  'use strict';

  // ==================== 配置 ====================
  const CONFIG = window.NAV_HUB_CONFIG || {};
  const API_BASE = CONFIG.apiBase || '';
  const CAN_MANAGE = CONFIG.canManage || false;

  // ==================== 状态 ====================
  let allCategories = [];
  let allItems = [];
  let currentCategory = 'all';
  let currentKeyword = '';
  let editingItemId = null;
  let editingCatId = null;

  // ==================== DOM 缓存 ====================
  const $ = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));

  // ==================== Toast ====================
  function showToast(msg, duration = 2500) {
    let toast = $('.nav-hub-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'nav-hub-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), duration);
  }

  // ==================== API 封装 ====================
  async function apiGet(url) {
    const r = await fetch(API_BASE + url, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    if (r.status === 401) {
      const data = await r.json().catch(() => ({}));
      if (data.need_login) {
        showToast('🔒 请先登录');
        return { ok: false, need_login: true };
      }
    }
    return r.json();
  }

  async function apiPost(url, body) {
    const r = await fetch(API_BASE + url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify(body)
    });
    if (r.status === 401) {
      const data = await r.json().catch(() => ({}));
      if (data.need_login) {
        showToast('🔒 请先登录');
        return { ok: false, need_login: true };
      }
    }
    return r.json();
  }

  async function apiPut(url, body) {
    const r = await fetch(API_BASE + url, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify(body)
    });
    if (r.status === 401) {
      const data = await r.json().catch(() => ({}));
      if (data.need_login) {
        showToast('🔒 请先登录');
        return { ok: false, need_login: true };
      }
    }
    return r.json();
  }

  async function apiDelete(url) {
    const r = await fetch(API_BASE + url, {
      method: 'DELETE',
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    if (r.status === 401) {
      const data = await r.json().catch(() => ({}));
      if (data.need_login) {
        showToast('🔒 请先登录');
        return { ok: false, need_login: true };
      }
    }
    return r.json();
  }

  // ==================== 数据加载 ====================
  async function loadCategories() {
    const res = await apiGet('/api/categories');
    if (res.code === 200) {
      allCategories = res.data.list || [];
      renderCategoryTabs();
      return allCategories;
    }
    return [];
  }

  async function loadItems() {
    const params = new URLSearchParams();
    if (currentCategory !== 'all') {
      params.set('category_id', currentCategory);
    }
    if (currentKeyword) {
      params.set('keyword', currentKeyword);
    }
    const res = await apiGet('/api/items?' + params.toString());
    if (res.code === 200) {
      allItems = res.data.list || [];
      renderItems();
    }
  }

  // ==================== 渲染：分类标签 ====================
  function renderCategoryTabs() {
    const container = $('#navHubCategories');
    if (!container) return;

    let html = `<span class="nav-hub-cat-tab ${currentCategory === 'all' ? 'active' : ''}" data-id="all">全部</span>`;
    allCategories.forEach(cat => {
      const active = String(currentCategory) === String(cat.id) ? 'active' : '';
      html += `<span class="nav-hub-cat-tab ${active}" data-id="${cat.id}">${cat.icon || '📂'} ${escapeHtml(cat.name)}</span>`;
    });
    container.innerHTML = html;

    // 绑定点击
    $$('.nav-hub-cat-tab', container).forEach(tab => {
      tab.addEventListener('click', () => {
        currentCategory = tab.dataset.id;
        renderCategoryTabs();
        loadItems();
      });
    });
  }

  // ==================== 渲染：导航项 ====================
  function renderItems() {
    const container = $('#navHubContent');
    if (!container) return;

    if (allItems.length === 0) {
      container.innerHTML = `
        <div class="nav-hub-empty">
          <span class="emoji">🧭</span>
          <p>暂无导航链接</p>
          ${CAN_MANAGE ? '<button class="nav-hub-btn nav-hub-btn--primary" onclick="NavHub.openItemModal()">+ 添加第一个导航</button>' : ''}
        </div>
      `;
      return;
    }

    // 按分类分组
    const grouped = {};
    allItems.forEach(item => {
      const catName = item.category_name || '未分类';
      const catIcon = item.category_icon || '📂';
      if (!grouped[catName]) grouped[catName] = { icon: catIcon, items: [] };
      grouped[catName].items.push(item);
    });

    // 按分类排序权重排序
    const catOrder = {};
    allCategories.forEach(c => catOrder[c.name] = c.sort_order || 0);
    const sortedCats = Object.keys(grouped).sort((a, b) => (catOrder[a] || 0) - (catOrder[b] || 0));

    let html = '';
    sortedCats.forEach(catName => {
      const group = grouped[catName];
      html += `
        <div class="nav-hub-section">
          <div class="nav-hub-section-header">
            <div class="nav-hub-section-title">
              <span class="emoji">${group.icon}</span>
              ${escapeHtml(catName)}
            </div>
          </div>
          <div class="nav-hub-grid">
            ${group.items.map(item => renderItemCard(item)).join('')}
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function renderItemCard(item) {
    const typeClass = item.type === 'doc' ? 'type-doc' : 'type-web';
    const typeTag = item.type === 'doc'
      ? '<span class="nav-hub-tag nav-hub-tag--doc">在线文档</span>'
      : '<span class="nav-hub-tag nav-hub-tag--web">网址</span>';
    const hotTag = item.is_hot ? '<span class="nav-hub-tag nav-hub-tag--hot">热门</span>' : '';
    const freeTag = item.tags && item.tags.includes('免费') ? '<span class="nav-hub-tag nav-hub-tag--free">免费</span>' : '';

    const manageBtns = CAN_MANAGE ? `
      <div class="nav-hub-card-actions">
        <button class="nav-hub-icon-btn" title="编辑" onclick="event.stopPropagation(); NavHub.editItem(${item.id})">✏️</button>
        <button class="nav-hub-icon-btn" title="删除" onclick="event.stopPropagation(); NavHub.deleteItem(${item.id})">🗑️</button>
      </div>
    ` : '';

    return `
      <div class="nav-hub-card ${typeClass}" onclick="NavHub.openLink('${escapeHtml(item.url)}', '${item.type}')">
        <div class="nav-hub-card-top">
          <div class="nav-hub-card-icon">${getTypeIcon(item.type)}</div>
          ${manageBtns}
        </div>
        <div class="nav-hub-card-title">${escapeHtml(item.name)}</div>
        <div class="nav-hub-card-desc">${escapeHtml(item.description || '')}</div>
        <div class="nav-hub-card-meta">
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${typeTag}
            ${hotTag}
            ${freeTag}
          </div>
          <button class="nav-hub-open-btn" onclick="event.stopPropagation(); NavHub.openLink('${escapeHtml(item.url)}', '${item.type}')">
            打开 →
          </button>
        </div>
      </div>
    `;
  }

  function getTypeIcon(type) {
    if (type === 'doc') return '📄';
    return '🌐';
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ==================== 打开链接 ====================
  function openLink(url, type) {
    if (!url) return;
    if (type === 'doc' || type === 'web') {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }

  // ==================== 弹窗控制 ====================
  function openModal(id) {
    const el = $(id);
    if (el) el.classList.add('show');
    document.body.style.overflow = 'hidden';
  }

  function closeModal(id) {
    const el = $(id);
    if (el) el.classList.remove('show');
    document.body.style.overflow = '';
  }

  function closeModalOnOverlay(e, id) {
    if (e.target === $(id)) closeModal(id);
  }

  // ==================== 导航项：增删改 ====================
  function openItemModal(itemId) {
    editingItemId = itemId || null;
    const modal = $('#navHubItemModal');
    const title = $('#navHubItemModalTitle');
    const form = $('#navHubItemForm');

    if (!modal || !form) return;

    // 填充分类下拉
    const catSelect = form.querySelector('[name="category_id"]');
    if (catSelect) {
      catSelect.innerHTML = '<option value="">-- 选择分类 --</option>' +
        allCategories.map(c => `<option value="${c.id}">${escapeHtml(c.icon || '')} ${escapeHtml(c.name)}</option>`).join('');
    }

    if (itemId) {
      const item = allItems.find(i => i.id === itemId);
      if (!item) return;
      title.textContent = '✏️ 编辑导航';
      form.querySelector('[name="name"]').value = item.name || '';
      form.querySelector('[name="url"]').value = item.url || '';
      form.querySelector('[name="type"]').value = item.type || 'web';
      if (catSelect) catSelect.value = item.category_id || '';
      form.querySelector('[name="description"]').value = item.description || '';
      form.querySelector('[name="sort_order"]').value = item.sort_order || 0;
      form.querySelector('[name="is_hot"]').checked = !!item.is_hot;
      form.querySelector('[name="tags"]').value = item.tags || '';
    } else {
      title.textContent = '➕ 添加导航';
      form.reset();
      form.querySelector('[name="sort_order"]').value = '0';
    }

    openModal('#navHubItemModal');
  }

  async function saveItem() {
    const form = $('#navHubItemForm');
    if (!form) return;

    const data = {
      name: form.querySelector('[name="name"]').value.trim(),
      url: form.querySelector('[name="url"]').value.trim(),
      type: form.querySelector('[name="type"]').value,
      category_id: form.querySelector('[name="category_id"]').value || null,
      description: form.querySelector('[name="description"]').value.trim(),
      sort_order: parseInt(form.querySelector('[name="sort_order"]').value) || 0,
      is_hot: form.querySelector('[name="is_hot"]').checked,
      tags: form.querySelector('[name="tags"]').value.trim()
    };

    if (!data.name) { showToast('❌ 名称不能为空'); return; }
    if (!data.url) { showToast('❌ 链接地址不能为空'); return; }

    let res;
    if (editingItemId) {
      res = await apiPut('/api/items/' + editingItemId, data);
    } else {
      res = await apiPost('/api/items', data);
    }

    if (res.code === 200) {
      showToast(editingItemId ? '✅ 更新成功' : '✅ 添加成功');
      closeModal('#navHubItemModal');
      await loadItems();
    } else {
      showToast('❌ ' + (res.msg || '操作失败'));
    }
  }

  async function editItem(id) {
    openItemModal(id);
  }

  async function deleteItem(id) {
    const item = allItems.find(i => i.id === id);
    if (!item) return;
    if (!confirm(`确定要删除「${item.name}」吗？`)) return;

    const res = await apiDelete('/api/items/' + id);
    if (res.code === 200) {
      showToast('✅ 已删除');
      await loadItems();
    } else {
      showToast('❌ ' + (res.msg || '删除失败'));
    }
  }

  // ==================== 分类：增删改 ====================
  function openCatModal(catId) {
    editingCatId = catId || null;
    const modal = $('#navHubCatModal');
    const title = $('#navHubCatModalTitle');
    const form = $('#navHubCatForm');

    if (!modal || !form) return;

    if (catId) {
      const cat = allCategories.find(c => c.id === catId);
      if (!cat) return;
      title.textContent = '✏️ 编辑分类';
      form.querySelector('[name="name"]').value = cat.name || '';
      form.querySelector('[name="icon"]').value = cat.icon || '';
      form.querySelector('[name="sort_order"]').value = cat.sort_order || 0;
    } else {
      title.textContent = '➕ 添加分类';
      form.reset();
      form.querySelector('[name="sort_order"]').value = '0';
    }

    openModal('#navHubCatModal');
  }

  async function saveCategory() {
    const form = $('#navHubCatForm');
    if (!form) return;

    const data = {
      name: form.querySelector('[name="name"]').value.trim(),
      icon: form.querySelector('[name="icon"]').value.trim(),
      sort_order: parseInt(form.querySelector('[name="sort_order"]').value) || 0
    };

    if (!data.name) { showToast('❌ 分类名称不能为空'); return; }

    let res;
    if (editingCatId) {
      res = await apiPut('/api/categories/' + editingCatId, data);
    } else {
      res = await apiPost('/api/categories', data);
    }

    if (res.code === 200) {
      showToast(editingCatId ? '✅ 分类更新成功' : '✅ 分类添加成功');
      closeModal('#navHubCatModal');
      await loadCategories();
      await loadItems();
      // 如果在分类管理页，刷新分类列表
      if (typeof renderCatManageList === 'function') renderCatManageList();
    } else {
      showToast('❌ ' + (res.msg || '操作失败'));
    }
  }

  async function deleteCategory(id) {
    const cat = allCategories.find(c => c.id === id);
    if (!cat) return;
    if (!confirm(`确定要删除分类「${cat.name}」吗？该分类下的所有导航项也将被删除！`)) return;

    const res = await apiDelete('/api/categories/' + id);
    if (res.code === 200) {
      showToast('✅ 分类已删除');
      await loadCategories();
      await loadItems();
      if (typeof renderCatManageList === 'function') renderCatManageList();
    } else {
      showToast('❌ ' + (res.msg || '删除失败'));
    }
  }

  // ==================== 搜索 ====================
  function onSearchInput(e) {
    currentKeyword = e.target.value.trim();
    loadItems();
  }

  // ==================== 初始化 ====================
  async function init() {
    // 绑定搜索
    const searchInput = $('#navHubSearch');
    if (searchInput) {
      let timer;
      searchInput.addEventListener('input', e => {
        clearTimeout(timer);
        timer = setTimeout(() => onSearchInput(e), 300);
      });
    }

    // 加载数据
    await loadCategories();
    await loadItems();
  }

  // ==================== 暴露全局 API ====================
  window.NavHub = {
    init,
    openLink,
    openItemModal: () => openItemModal(),
    editItem,
    deleteItem,
    saveItem,
    openCatModal: () => openCatModal(),
    editCat: openCatModal,
    deleteCat: deleteCategory,
    saveCategory,
    openModal,
    closeModal,
    closeModalOnOverlay,
    showToast,
    loadCategories,
    loadItems
  };

  // 页面加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
