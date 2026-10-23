/* 看板娘面板：从 base.html 内联脚本抽出（行为不变） */
(function () {
    const POS_KEY = 'kanban_pos';
    const GREET_KEY = 'kanban_greeted';
    const kanban = document.getElementById('kanban');
    const img = document.getElementById('kanban-img');
    const wrap = document.getElementById('kanban-bubble-wrap');
    const bubble = document.getElementById('kanban-bubble');
    const tail = document.getElementById('kanban-tail');
    if (!kanban || !img || !wrap || !bubble) return;

    // ========== 尺寸：全部由 JS 控制 ==========
    function applySize() {
        const isMobile = window.innerWidth <= 700;
        if (isMobile) {
            img.style.setProperty('width', '180px', 'important');
            img.style.setProperty('height', 'auto', 'important');
            bubble.style.setProperty('font-size', '1rem', 'important');
            bubble.style.setProperty('max-width', '270px', 'important');
            bubble.style.setProperty('padding', '12px 18px', 'important');
            tail.style.setProperty('right', '20px', 'important');
        } else {
            img.style.setProperty('width', '120px', 'important');
            img.style.setProperty('height', 'auto', 'important');
            bubble.style.setProperty('font-size', '0.85rem', 'important');
            bubble.style.setProperty('max-width', '220px', 'important');
            bubble.style.setProperty('padding', '10px 16px', 'important');
            tail.style.setProperty('right', '24px', 'important');
        }
    }
    applySize();
    window.addEventListener('resize', applySize);

    // ========== 位置恢复 ==========
    function clampPosition() {
        const rect = kanban.getBoundingClientRect();
        let left = rect.left;
        let top = rect.top;
        const maxLeft = window.innerWidth - rect.width - 4;
        const maxTop = window.innerHeight - rect.height - 4;
        if (left < 4) left = 4;
        if (top < 4) top = 4;
        if (left > maxLeft) left = maxLeft;
        if (top > maxTop) top = maxTop;
        kanban.style.left = left + 'px';
        kanban.style.top = top + 'px';
        kanban.style.right = 'auto';
        kanban.style.bottom = 'auto';
    }

    function restorePosition() {
        try {
            const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
            if (saved && typeof saved.left === 'number' && typeof saved.top === 'number') {
                kanban.style.left = saved.left + 'px';
                kanban.style.top = saved.top + 'px';
                kanban.style.right = 'auto';
                kanban.style.bottom = 'auto';
                clampPosition();
            }
        } catch (e) {}
    }
    restorePosition();
    window.addEventListener('resize', clampPosition);

    // ========== 气泡 ==========
    let facts = [];
    let chitchat = ['欢迎来到动漫社！'];
    let lastChitchatIdx = -1;
    let typingTimer = null;
    let hideTimer = null;

    function hideBubble() {
        wrap.style.opacity = '0';
        wrap.style.transform = 'scale(0.6)';
    }

    function typeText(text) {
        clearTimeout(typingTimer);
        clearTimeout(hideTimer);
        bubble.textContent = '';
        let i = 0;
        (function tick() {
            if (i <= text.length) {
                bubble.textContent = text.slice(0, i);
                i++;
                typingTimer = setTimeout(tick, 40);
            } else {
                hideTimer = setTimeout(hideBubble, 8000);
            }
        })();
    }

    function showBubble(text) {
        clearTimeout(typingTimer);
        clearTimeout(hideTimer);
        wrap.style.opacity = '0';
        wrap.style.transform = 'scale(0.6)';
        bubble.textContent = '';
        setTimeout(() => {
            wrap.style.opacity = '1';
            wrap.style.transform = 'scale(1)';
            typeText(text);
        }, 200);
    }

    function randomChitchat() {
        if (!chitchat.length) return '……';
        if (chitchat.length === 1) return chitchat[0];
        let i;
        do {
            i = Math.floor(Math.random() * chitchat.length);
        } while (i === lastChitchatIdx);
        lastChitchatIdx = i;
        return chitchat[i];
    }

    function next() {
        if (facts.length) {
            showBubble(facts.shift());
        } else {
            showBubble(randomChitchat());
        }
    }

    // ========== 拖拽 ==========
    let dragging = false;
    let moved = false;
    let startX = 0, startY = 0, startLeft = 0, startTop = 0;

    function getPoint(e) {
        if (e.touches && e.touches[0]) return { x: e.touches[0].clientX, y: e.touches[0].clientY };
        return { x: e.clientX, y: e.clientY };
    }

    function onStart(e) {
        if (e.button !== undefined && e.button !== 0) return;
        const p = getPoint(e);
        const rect = kanban.getBoundingClientRect();
        startX = p.x;
        startY = p.y;
        startLeft = rect.left;
        startTop = rect.top;
        dragging = true;
        moved = false;
        img.style.cursor = 'grabbing';
    }

    function onMove(e) {
        if (!dragging) return;
        const p = getPoint(e);
        const dx = p.x - startX;
        const dy = p.y - startY;
        if (!moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) moved = true;
        if (!moved) return;

        let newLeft = startLeft + dx;
        let newTop = startTop + dy;
        const rect = kanban.getBoundingClientRect();
        const maxLeft = window.innerWidth - rect.width - 4;
        const maxTop = window.innerHeight - rect.height - 4;
        if (newLeft < 4) newLeft = 4;
        if (newTop < 4) newTop = 4;
        if (newLeft > maxLeft) newLeft = maxLeft;
        if (newTop > maxTop) newTop = maxTop;

        kanban.style.left = newLeft + 'px';
        kanban.style.top = newTop + 'px';
        kanban.style.right = 'auto';
        kanban.style.bottom = 'auto';
        if (e.cancelable) e.preventDefault();
    }

    function onEnd() {
        if (!dragging) return;
        dragging = false;
        img.style.cursor = 'grab';
        if (moved) {
            const rect = kanban.getBoundingClientRect();
            try {
                localStorage.setItem(POS_KEY, JSON.stringify({ left: rect.left, top: rect.top }));
            } catch (e) {}
        }
    }

    img.addEventListener('mousedown', onStart);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onEnd);

    img.addEventListener('touchstart', onStart, { passive: true });
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd);
    document.addEventListener('touchcancel', onEnd);

    // ========== 点击 ==========
    img.addEventListener('click', function () {
        if (moved) return;
        next();
    });
    bubble.addEventListener('click', next);

    // ========== 欢迎语：本次会话只弹一次 ==========
    fetch('/api/kanban/lines')
        .then(r => r.json())
        .then(data => {
            facts = data.facts || [];
            chitchat = data.chitchat || ['欢迎来到动漫社！'];

            // sessionStorage：同一个标签页内只弹一次
            // 新标签页 / 关掉重开 → 再弹一次
            if (!sessionStorage.getItem(GREET_KEY) && facts.length) {
                sessionStorage.setItem(GREET_KEY, '1');
                setTimeout(() => showBubble(facts.shift()), 800);
            }
        })
        .catch(() => {
            chitchat = ['欢迎来到动漫社！'];
        });
})();
