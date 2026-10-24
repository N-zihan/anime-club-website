/* 投票页实时票数 + 对抗进度条
 * 轮询 /api/contest/<id>/vote-stats，不依赖 Supabase 配置；
 * Supabase Realtime（若已配置）仍然工作，两边只更新同一批数字，不会冲突。
 *
 * 头部「实时 · HH:MM:SS」是**每秒走一格的北京时间时钟**（不是数据快照时间）：
 * - 数据快照时间放在悬停提示 title 里；
 * - 拉取失败时显示「更新暂停」，但时钟继续走，方便判断页面是不是还活着。
 */
(function () {
    'use strict';

    var root = document.getElementById('voteLive');
    if (!root) return;

    var contestId = root.dataset.contest;
    var gender = root.dataset.gender || 'female';
    var mode = root.dataset.mode || 'group';
    var stateEl = document.getElementById('voteLiveState');
    var POLL_MS = 10000;
    var timer = null;
    var clockTimer = null;
    var alive = true;
    var lastSyncedAt = '';

    function beijingClock() {
        try {
            return new Date().toLocaleTimeString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        } catch (e) {
            // 极老浏览器不支持 timeZone 选项：退回本地时间，至少时钟还在走
            return new Date().toTimeString().slice(0, 8);
        }
    }

    function paintState() {
        if (!stateEl) return;
        stateEl.textContent = (alive ? '实时' : '更新暂停') + ' · ' + beijingClock();
        stateEl.classList.toggle('stale', !alive);
        root.title = lastSyncedAt ? ('票数快照时间 ' + lastSyncedAt) : '';
    }

    function sharePercent(a, b) {
        var total = a + b;
        if (!total) return 50;
        return Math.round((a / total) * 100);
    }

    function setWidth(el, percent) {
        if (el) el.style.width = percent + '%';
    }

    function updateVsBars(totals) {
        document.querySelectorAll('.vote-vs-bar').forEach(function (bar) {
            var a = String(bar.dataset.vsA || '');
            var b = String(bar.dataset.vsB || '');
            var va = totals[a] || 0;
            var vb = totals[b] || 0;
            var pa = sharePercent(va, vb);
            setWidth(bar.querySelector('.vote-vs-left'), pa);
            setWidth(bar.querySelector('.vote-vs-right'), 100 - pa);
            bar.classList.toggle('is-empty', va + vb === 0);

            var meta = bar.parentElement ? bar.parentElement.querySelector('.vote-vs-meta') : null;
            if (meta) {
                var leftPct = meta.querySelector('.vote-vs-pct[data-side="a"]');
                var rightPct = meta.querySelector('.vote-vs-pct[data-side="b"]');
                var totalEl = meta.querySelector('.vote-vs-total');
                if (leftPct) leftPct.textContent = pa + '%';
                if (rightPct) rightPct.textContent = (100 - pa) + '%';
                if (totalEl) totalEl.textContent = '共 ' + (va + vb) + ' 票';
            }
        });
    }

    function updateShareBars(totals) {
        var sum = Object.keys(totals).reduce(function (acc, key) {
            return acc + (totals[key] || 0);
        }, 0);
        document.querySelectorAll('.vote-share-fill').forEach(function (fill) {
            var cid = String(fill.dataset.candidateId || '');
            var votes = totals[cid] || 0;
            var percent = sum ? Math.round((votes / sum) * 100) : 0;
            fill.style.width = percent + '%';
            var pctEl = document.querySelector('.vote-share-pct[data-candidate-id="' + cid + '"]');
            if (pctEl) pctEl.textContent = percent + '%';
        });
    }

    function updateNumbers(totals) {
        var selector = mode === 'qualifying' ? '.total-votes' : '.vote-count';
        document.querySelectorAll(selector + '[data-candidate-id]').forEach(function (el) {
            var cid = String(el.dataset.candidateId || '');
            if (Object.prototype.hasOwnProperty.call(totals, cid)) {
                el.textContent = totals[cid];
            }
        });
    }

    function apply(data) {
        var totals = data.totals || {};
        updateNumbers(totals);
        if (mode === 'qualifying') {
            updateShareBars(totals);
        } else {
            updateVsBars(totals);
        }
        alive = true;
        lastSyncedAt = data.updated_at || '';
        paintState();
    }

    function refresh() {
        fetch('/api/contest/' + contestId + '/vote-stats?mode=' + encodeURIComponent(mode)
              + '&gender=' + encodeURIComponent(gender),
              {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
            .then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function (data) {
                if (data && data.ok) apply(data);
            })
            .catch(function () {
                alive = false;
                paintState();
            });
    }

    // 时钟每秒走一格；数据每 10 秒拉一次（页面不可见时暂停拉取）
    paintState();
    clockTimer = setInterval(paintState, 1000);
    setTimeout(refresh, 1200);
    timer = setInterval(function () {
        if (!document.hidden) refresh();
    }, POLL_MS);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            refresh();
            paintState();
        }
    });
    window.addEventListener('beforeunload', function () {
        if (timer) clearInterval(timer);
        if (clockTimer) clearInterval(clockTimer);
    });
})();
