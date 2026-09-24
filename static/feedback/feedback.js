/* 反馈模块全局脚本（static/feedback/feedback.js）。
   负责：反馈中心页面的提交表单、我的反馈列表、管理列表、开发者回复/状态/优先级、
   管理员导航角标、轻提示 toast。所有用户内容用 textContent 渲染，杜绝 XSS。
   注：原「全局悬浮按钮 + 提交弹窗」已移除，提交表单内联于反馈中心页面。 */
(function () {
  "use strict";

  var TYPE_LABEL = { bug: "🐞 问题", suggestion: "💡 建议", idea: "✨ 想法" };
  var STATUS_LABEL = {
    pending: "待处理", acknowledged: "已受理", in_progress: "处理中",
    resolved: "已解决", closed: "已关闭"
  };
  var PRIORITY_LABEL = { low: "低", medium: "中", high: "高" };

  function postJSON(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(payload || {})
    });
  }

  function showToast(msg) {
    var t = document.createElement("div");
    t.className = "fb-toast";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  /* ---------- 提交反馈（表单内联于反馈中心页面，无全局悬浮按钮） ---------- */
  var submitBtn = document.getElementById("fbSubmit");
  if (submitBtn) submitBtn.addEventListener("click", submitFeedback);

  function submitFeedback() {
    var titleEl = document.getElementById("fbTitle");
    var contentEl = document.getElementById("fbContent");
    var typeEl = document.getElementById("fbType");
    var moduleEl = document.getElementById("fbModule");
    var msg = document.getElementById("fbMsg");
    var title = titleEl.value.trim();
    var content = contentEl.value.trim();
    if (!title || !content) {
      if (msg) msg.textContent = "标题和详细描述不能为空";
      return;
    }
    postJSON("/feedback/api/create", {
      type: typeEl.value,
      title: title,
      content: content,
      module: moduleEl.value
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code === 200) {
        titleEl.value = ""; contentEl.value = "";
        if (msg) msg.textContent = "";
        showToast("反馈已提交，感谢！");
        refreshMyList();
        refreshBadge();
      } else {
        if (msg) msg.textContent = j.msg || "提交失败";
      }
    }).catch(function () {
      if (msg) msg.textContent = "网络错误，请重试";
    });
  }

  /* ---------- 我的反馈 ---------- */
  function refreshMyList() {
    var box = document.getElementById("fbMyList");
    if (!box) return;
    fetch("/feedback/api/list", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.code !== 200) {
          box.textContent = "请登录后查看你的反馈。";
          return;
        }
        renderList(box, j.data.items);
        if (!j.data.items.length) {
          box.innerHTML = "";
          var p = document.createElement("p");
          p.className = "fb-empty";
          p.textContent = "还没有反馈，在上方「提交反馈」告诉我们你的想法吧。";
          box.appendChild(p);
        }
      })
      .catch(function () { box.textContent = "加载失败"; });
  }

  function renderList(box, items) {
    box.innerHTML = "";
    items.forEach(function (it) {
      box.appendChild(buildCard(it, false));
    });
  }

  function buildCard(it, isAdmin) {
    var card = document.createElement("div");
    card.className = "fb-card" + (isAdmin ? " fb-card-admin" : "");

    var head = document.createElement("div");
    head.className = "fb-card-head";
    var badge = document.createElement("span");
    badge.className = "fb-type fb-type-" + it.type;
    badge.textContent = TYPE_LABEL[it.type] || it.type;
    var title = document.createElement("span");
    title.className = "fb-card-title";
    title.textContent = it.title;
    head.appendChild(badge); head.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "fb-card-meta";
    meta.textContent = (STATUS_LABEL[it.status] || it.status) + " · 优先级" +
      (PRIORITY_LABEL[it.priority] || it.priority) +
      (it.module ? (" · " + it.module) : "") +
      (isAdmin ? (" · " + (it.author_nickname || it.author_account)) : "");

    var content = document.createElement("div");
    content.className = "fb-card-content";
    content.textContent = it.content;

    var foot = document.createElement("div");
    foot.className = "fb-card-foot";
    var link = document.createElement("a");
    link.href = "/feedback/" + it.id;
    link.className = "fb-link";
    link.textContent = "查看详情 →";
    foot.appendChild(link);
    if (it.dev_reply) {
      var dr = document.createElement("div");
      dr.className = "fb-devreply";
      dr.textContent = "开发者回复：" + it.dev_reply;
      foot.appendChild(dr);
    }

    card.appendChild(head); card.appendChild(meta); card.appendChild(content); card.appendChild(foot);
    return card;
  }

  /* ---------- 管理列表 ---------- */
  function refreshAdmin() {
    var box = document.getElementById("fbAdminList");
    if (!box) return;
    var statusEl = document.getElementById("fbFilterStatus");
    var typeEl = document.getElementById("fbFilterType");
    var moduleEl = document.getElementById("fbFilterModule");
    var qs = new URLSearchParams();
    if (statusEl && statusEl.value) qs.set("status", statusEl.value);
    if (typeEl && typeEl.value) qs.set("type", typeEl.value);
    if (moduleEl && moduleEl.value) qs.set("module", moduleEl.value);
    fetch("/feedback/api/admin?" + qs.toString(), { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.code !== 200) { box.textContent = "无权限或加载失败"; return; }
        box.innerHTML = "";
        if (!j.data.items.length) {
          var p = document.createElement("p");
          p.className = "fb-empty";
          p.textContent = "暂无符合条件的反馈。";
          box.appendChild(p);
          return;
        }
        j.data.items.forEach(function (it) {
          box.appendChild(buildAdminCard(it));
        });
      })
      .catch(function () { box.textContent = "加载失败"; });
  }

  function buildAdminCard(it) {
    var card = buildCard(it, true);

    var statusSel = document.createElement("select");
    statusSel.className = "fb-select fb-sel-status";
    statusSel.setAttribute("data-item", it.id);
    ["pending", "acknowledged", "in_progress", "resolved", "closed"].forEach(function (s) {
      var o = document.createElement("option");
      o.value = s; o.textContent = STATUS_LABEL[s];
      if (s === it.status) o.selected = true;
      statusSel.appendChild(o);
    });

    var priSel = document.createElement("select");
    priSel.className = "fb-select fb-sel-priority";
    priSel.setAttribute("data-item", it.id);
    ["low", "medium", "high"].forEach(function (p) {
      var o = document.createElement("option");
      o.value = p; o.textContent = PRIORITY_LABEL[p];
      if (p === it.priority) o.selected = true;
      priSel.appendChild(o);
    });

    var reply = document.createElement("textarea");
    reply.className = "fb-input fb-reply-input";
    reply.setAttribute("data-item", it.id);
    reply.rows = 2;
    reply.placeholder = "填写开发者回复（提交后自动置为「已受理」）";
    reply.value = it.dev_reply || "";

    var replyBtn = document.createElement("button");
    replyBtn.type = "button";
    replyBtn.className = "fb-btn-primary fb-reply-btn";
    replyBtn.setAttribute("data-item", it.id);
    replyBtn.textContent = "回复并受理";

    var row1 = document.createElement("div");
    row1.className = "fb-admin-row";
    var lblS = document.createElement("label"); lblS.textContent = "状态";
    var lblP = document.createElement("label"); lblP.textContent = "优先级";
    row1.appendChild(lblS); row1.appendChild(statusSel); row1.appendChild(lblP); row1.appendChild(priSel);

    var row2 = document.createElement("div");
    row2.className = "fb-admin-row";
    row2.appendChild(reply);

    var row3 = document.createElement("div");
    row3.className = "fb-admin-row";
    row3.appendChild(replyBtn);

    card.appendChild(row1); card.appendChild(row2); card.appendChild(row3);
    return card;
  }

  /* ---------- 管理员操作（事件委托，兼容管理列表与详情页） ---------- */
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.classList.contains("fb-sel-status")) setStatus(t.getAttribute("data-item"), t.value);
    else if (t.classList.contains("fb-sel-priority")) setPriority(t.getAttribute("data-item"), t.value);
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".fb-reply-btn") : null;
    if (b) {
      var card = b.closest(".fb-card");
      var ta = card ? card.querySelector(".fb-reply-input") : null;
      sendReply(b.getAttribute("data-item"), ta ? ta.value : "");
    }
  });

  function setStatus(id, status) {
    postJSON("/feedback/api/" + id + "/status", { status: status })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.code === 200) { showToast("状态已更新"); refreshAdmin(); refreshBadge(); }
        else showToast(j.msg || "操作失败");
      });
  }
  function setPriority(id, priority) {
    postJSON("/feedback/api/" + id + "/priority", { priority: priority })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.code === 200) { showToast("优先级已更新"); refreshAdmin(); }
        else showToast(j.msg || "操作失败");
      });
  }
  function sendReply(id, reply) {
    if (!reply || !reply.trim()) { showToast("回复内容不能为空"); return; }
    postJSON("/feedback/api/" + id + "/reply", { dev_reply: reply.trim() })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.code === 200) {
          showToast("已回复");
          refreshAdmin();
          refreshBadge();
          // 若在详情页，刷新开发者回复展示
          var dr = document.querySelector(".fb-detail .fb-devreply-text");
          if (dr) dr.textContent = reply.trim();
        } else showToast(j.msg || "操作失败");
      });
  }

  /* ---------- 管理员导航角标（待处理数） ---------- */
  function refreshBadge() {
    fetch("/feedback/api/admin?status=pending&per_page=1", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) {
        if (r.status !== 200) return null; // 非管理员
        return r.json();
      })
      .then(function (j) {
        if (!j || j.code !== 200) return;
        var total = (j.data && j.data.total) || 0;
        var nav = document.querySelector('a.nav-item[data-module="feedback"]');
        if (!nav) return;
        var b = nav.querySelector(".fb-badge");
        if (total > 0) {
          if (!b) { b = document.createElement("span"); b.className = "fb-badge"; nav.appendChild(b); }
          b.textContent = total > 99 ? "99+" : String(total);
        } else if (b) {
          b.remove();
        }
      })
      .catch(function () {});
  }

  /* ---------- 初始化 ---------- */
  if (document.getElementById("fbMyList")) refreshMyList();
  if (document.getElementById("fbAdminList")) {
    ["fbFilterStatus", "fbFilterType", "fbFilterModule"].forEach(function (id) {
      var s = document.getElementById(id);
      if (s) s.addEventListener("change", refreshAdmin);
    });
    refreshAdmin();
  }
  refreshBadge();
})();
