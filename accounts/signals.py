"""Signals for accounts app, including authentication tracking."""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import LoginHistory


def parse_device_info(user_agent: str) -> str:
    """Extract human-readable device and browser name from User-Agent string."""
    if not user_agent:
        return "Unknown Device"
    ua = user_agent.lower()

    # Detect OS / Platform
    os_name = "Desktop"
    if "windows" in ua:
        os_name = "Windows"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "macOS"
    elif "linux" in ua:
        os_name = "Linux"

    # Detect Browser
    browser = "Browser"
    if "edg" in ua:
        browser = "Edge"
    elif "chrome" in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    elif "opera" in ua or "opr" in ua:
        browser = "Opera"

    return f"{browser} ({os_name})"


@receiver(user_logged_in)
def record_user_login(sender, request, user, **kwargs):
    """Automatically log user login events with client IP and browser info."""
    if not request or not user:
        return

    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR")

    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:500]
    device = parse_device_info(user_agent)

    LoginHistory.objects.create(
        user=user,
        ip_address=ip,
        user_agent=user_agent,
        device=device,
    )
