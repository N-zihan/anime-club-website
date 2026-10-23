/* 萌战分享海报：前端 canvas 画 1200x630 PNG（不依赖服务器字体） */
(function () {
    'use strict';

    var btn = document.getElementById('posterBtn');
    var dataEl = document.getElementById('posterData');
    if (!btn || !dataEl) return;

    var data;
    try {
        data = JSON.parse(dataEl.textContent || '{}');
    } catch (e) {
        data = {};
    }

    var W = 1200;
    var H = 630;
    var CLUB = data.club || '动漫社';
    var SITE = data.site || '';

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
    }

    function wrapText(ctx, text, maxWidth, maxLines) {
        var lines = [];
        var line = '';
        for (var i = 0; i < text.length; i++) {
            var test = line + text[i];
            if (ctx.measureText(test).width > maxWidth && line) {
                lines.push(line);
                line = text[i];
                if (lines.length === maxLines) break;
            } else {
                line = test;
            }
        }
        if (line && lines.length < maxLines) lines.push(line);
        return lines;
    }

    function draw() {
        var canvas = document.createElement('canvas');
        canvas.width = W;
        canvas.height = H;
        var ctx = canvas.getContext('2d');

        // 背景
        var bg = ctx.createLinearGradient(0, 0, W, H);
        bg.addColorStop(0, '#eef2ff');
        bg.addColorStop(0.55, '#fdf2f8');
        bg.addColorStop(1, '#e0f2fe');
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, W, H);

        // 装饰圆
        ctx.fillStyle = 'rgba(224, 108, 154, 0.12)';
        ctx.beginPath();
        ctx.arc(1110, 80, 160, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = 'rgba(91, 155, 234, 0.12)';
        ctx.beginPath();
        ctx.arc(90, 610, 200, 0, Math.PI * 2);
        ctx.fill();

        // 顶部小字
        ctx.fillStyle = '#64748b';
        ctx.font = '600 22px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
        ctx.fillText('SAIMOE · 萌战战报', 56, 72);

        var phase = data.phase_label || '';
        if (phase) {
            ctx.font = '500 20px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
            var pw = ctx.measureText(phase).width + 34;
            roundRect(ctx, 56, 92, pw, 40, 20);
            ctx.fillStyle = 'rgba(91, 155, 234, 0.15)';
            ctx.fill();
            ctx.fillStyle = '#3f7fd0';
            ctx.fillText(phase, 73, 120);
        }

        // 标题
        ctx.fillStyle = '#0f172a';
        ctx.font = '700 44px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
        var titleLines = wrapText(ctx, data.title || '萌战', 620, 2);
        titleLines.forEach(function (line, i) {
            ctx.fillText(line, 56, 190 + i * 54);
        });

        // 总票数
        ctx.font = '500 20px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
        ctx.fillStyle = '#64748b';
        ctx.fillText('累计票数 ' + (data.total_votes || 0), 56, 300);

        // 票数榜
        var entries = (data.entries || []).slice(0, 6);
        var maxVotes = entries.reduce(function (m, e) {
            return Math.max(m, e.votes || 0);
        }, 0) || 1;
        var startY = 340;
        var rowH = 38;
        entries.forEach(function (entry, i) {
            var y = startY + i * rowH;
            ctx.fillStyle = '#334155';
            ctx.font = '500 20px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
            var name = entry.name || '';
            if (name.length > 16) name = name.slice(0, 16) + '…';
            ctx.fillText((i + 1) + '. ' + name, 56, y + 16);

            var barX = 360;
            var barW = 640;
            var ratio = Math.max(0.03, (entry.votes || 0) / maxVotes);
            roundRect(ctx, barX, y, barW, 22, 11);
            ctx.fillStyle = 'rgba(148, 163, 184, 0.22)';
            ctx.fill();

            var grad = ctx.createLinearGradient(barX, 0, barX + barW, 0);
            i % 2 === 0 ? (grad.addColorStop(0, '#5b9bea'), grad.addColorStop(1, '#3f7fd0'))
                        : (grad.addColorStop(0, '#e46c9a'), grad.addColorStop(1, '#c74f80'));
            roundRect(ctx, barX, y, Math.max(26, barW * ratio), 22, 11);
            ctx.fillStyle = grad;
            ctx.fill();

            ctx.fillStyle = '#0f172a';
            ctx.font = '700 20px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
            ctx.fillText(String(entry.votes || 0), barX + barW + 16, y + 17);
        });

        // 页脚
        ctx.fillStyle = '#94a3b8';
        ctx.font = '500 18px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
        ctx.fillText(CLUB + (SITE ? ' · ' + SITE : ''), 56, H - 42);
        var today = new Date();
        var stamp = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0')
            + '-' + String(today.getDate()).padStart(2, '0');
        ctx.textAlign = 'right';
        ctx.fillText(stamp, W - 56, H - 42);
        ctx.textAlign = 'left';

        return canvas;
    }

    function download(canvas) {
        var name = 'saimoe-poster-' + new Date().getTime() + '.png';
        if (canvas.toBlob) {
            canvas.toBlob(function (blob) {
                if (!blob) return;
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
            }, 'image/png');
        } else {
            var a2 = document.createElement('a');
            a2.href = canvas.toDataURL('image/png');
            a2.download = name;
            a2.click();
        }
    }

    btn.addEventListener('click', function () {
        btn.disabled = true;
        try {
            var canvas = draw();
            download(canvas);
        } catch (e) {
            // 极端情况下（比如内存不足）给用户一个提示
            alert('生成海报失败，请稍后再试');
        } finally {
            setTimeout(function () { btn.disabled = false; }, 600);
        }
    });
})();
