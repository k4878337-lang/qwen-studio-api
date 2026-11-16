#!/data/user/0/com.termux/files/usr/bin/bash
# ==============================================================
# Qwen Studio - 24/7 OpenAI-Compatible API Server Launcher
# ==============================================================

echo "=================================================="
echo "  Starting Qwen Studio 24/7 Server & Tunnel"
echo "=================================================="

# 1. Prevent Android from putting Termux to sleep
if type termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
    echo "[✓] Termux Wake Lock enabled (Android sleep prevented)"
fi

# 2. Terminate any previous server instances cleanly
python3 -c "
import os, signal
for p in os.listdir('/proc'):
    if p.isdigit():
        try:
            with open(f'/proc/{p}/cmdline', 'rb') as f:
                c = f.read().decode('utf-8', errors='ignore')
                if 'server.py' in c and p != str(os.getpid()):
                    os.kill(int(p), signal.SIGKILL)
        except Exception:
            pass
" 2>/dev/null
sleep 1

# 3. Change directory and launch server
cd /data/user/0/com.termux/files/home/projects/qwen-studio-web
exec python3 -u server.py 5000
