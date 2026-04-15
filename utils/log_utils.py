from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Mapping

EVENT_VERSION = 1
_TEXT_SHOW_EVENT_META = False


class EventStability(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"


class LogAction(str, Enum):
    APP_STARTED = "app_started"
    APP_UI_RUNNING = "app_ui_running"
    APP_UI_EXITED = "app_ui_exited"
    APP_EXIT_REQUESTED = "app_exit_requested"
    AUTOSTART_UPDATE_FAILED = "autostart_update_failed"
    BAD_HANDSHAKE = "bad_handshake"
    CFPROXY_DOMAINS_FETCH_FAILED = "cfproxy_domains_fetch_failed"
    CFPROXY_DOMAINS_UPDATED = "cfproxy_domains_updated"
    CFPROXY_ENABLED = "cfproxy_enabled"
    CF_FALLBACK_ATTEMPT = "cf_fallback_attempt"
    CF_FALLBACK_CONNECT_FAILED = "cf_fallback_connect_failed"
    CF_ACTIVE_DOMAIN_SWITCHED = "cf_active_domain_switched"
    CHECK_UPDATES_TOGGLED = "check_updates_toggled"
    CLIENT_DISCONNECTED = "client_disconnected"
    CLIENT_DISCONNECTED_BEFORE_HANDSHAKE = "client_disconnected_before_handshake"
    CLIENT_DISCONNECTED_PROXY_HEADER = "client_disconnected_proxy_header"
    CLIENT_TASK_CANCELLED = "client_task_cancelled"
    CLIPBOARD_COPY_FAILED = "clipboard_copy_failed"
    CONFIG_DC_IP_INVALID = "config_dc_ip_invalid"
    CONFIG_LOAD_FAILED = "config_load_failed"
    CONFIG_LOADED = "config_loaded"
    CONFIG_SAVED = "config_saved"
    CONNECTION_ABORTED_LOCAL_SYSTEM = "connection_aborted_local_system"
    CONNECTION_RESET = "connection_reset"
    CONNECT_LINK_DD = "connect_link_dd"
    CONNECT_LINK_EE = "connect_link_ee"
    CONNECT_SECRET_STANDARD = "connect_secret_standard"
    CONSOLE_MODE_FALLBACK = "console_mode_fallback"
    CTK_DIALOG_FAILED = "ctk_dialog_failed"
    DC_TARGET = "dc_target"
    FAKE_TLS_ENABLED = "fake_tls_enabled"
    FAKE_TLS_HANDSHAKE_OK = "fake_tls_handshake_ok"
    FAKE_TLS_VERIFY_FAILED_MASKING = "fake_tls_verify_failed_masking"
    FALLBACK_CLOSED = "fallback_closed"
    FALLBACK_DUE_TO_MISSING_DC = "fallback_due_to_missing_dc"
    FALLBACK_DUE_TO_WS_BLACKLIST = "fallback_due_to_ws_blacklist"
    FALLBACK_UNAVAILABLE = "fallback_unavailable"
    HANDSHAKE_OK = "handshake_ok"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    INVALID_DC_IP_ARG = "invalid_dc_ip_arg"
    INVALID_SECRET_HEX = "invalid_secret_hex"
    INVALID_SECRET_LENGTH = "invalid_secret_length"
    LINK_COPY_REQUESTED = "link_copy_requested"
    LINK_OPEN_REQUESTED = "link_open_requested"
    LOGGING_RECONFIGURED = "logging_reconfigured"
    LOG_FILE_READY = "log_file_ready"
    MASKING_CONNECTED = "masking_connected"
    MASKING_CONNECT_FAILED = "masking_connect_failed"
    MENUBAR_RUNTIME_READY = "menubar_runtime_ready"
    MSG_SPLITTER_ACTIVATED = "msg_splitter_activated"
    NON_TLS_REDIRECT_SENT = "non_tls_redirect_sent"
    OBFS_INIT_INCOMPLETE_INSIDE_TLS = "obfs_init_incomplete_inside_tls"
    OPEN_LOGS_REQUESTED = "open_logs_requested"
    PROXY_CONFIG_APPLIED = "proxy_config_applied"
    PROXY_PROTOCOL_HEADER = "proxy_protocol_header"
    PROXY_PROTOCOL_HEADER_INVALID = "proxy_protocol_header_invalid"
    PROXY_RESTARTING = "proxy_restarting"
    PROXY_SERVER_STARTED = "proxy_server_started"
    PROXY_STARTING = "proxy_starting"
    PROXY_START_SKIPPED = "proxy_start_skipped"
    PROXY_STOPPED = "proxy_stopped"
    PROXY_THREAD_CRASHED = "proxy_thread_crashed"
    RELAY_FORWARD_ENDED = "relay_forward_ended"
    RELAY_TCP_TO_WS_ENDED = "relay_tcp_to_ws_ended"
    RELAY_WS_TO_TCP_ENDED = "relay_ws_to_tcp_ended"
    RUNTIME = "runtime"
    RUNTIME_STATS = "runtime_stats"
    SECRET_GENERATED = "secret_generated"
    SHUTDOWN_KEYBOARD_INTERRUPT = "shutdown_keyboard_interrupt"
    TCP_FALLBACK_ATTEMPT = "tcp_fallback_attempt"
    TCP_FALLBACK_CONNECT_FAILED = "tcp_fallback_connect_failed"
    TELEGRAM_OPEN_FALLBACK_CLIPBOARD = "telegram_open_fallback_clipboard"
    TELEGRAM_OPEN_FALLBACK_WEBBROWSER = "telegram_open_fallback_webbrowser"
    TLS_RECORD_BODY_INCOMPLETE = "tls_record_body_incomplete"
    TLS_RECORD_HEADER_INCOMPLETE = "tls_record_header_incomplete"
    UNEXPECTED_CLIENT_ERROR = "unexpected_client_error"
    UNEXPECTED_OS_ERROR = "unexpected_os_error"
    UPDATE_CHECK_FAILED = "update_check_failed"
    WS_BLACKLISTED_ALL_REDIRECTS = "ws_blacklisted_all_redirects"
    WS_CONNECT_ATTEMPT = "ws_connect_attempt"
    WS_CONNECT_FAILED = "ws_connect_failed"
    WS_COOLDOWN_STARTED = "ws_cooldown_started"
    WS_HANDSHAKE_FAILED = "ws_handshake_failed"
    WS_POOL_CONNECTION_USED = "ws_pool_connection_used"
    WS_POOL_HIT = "ws_pool_hit"
    WS_POOL_REFILLED = "ws_pool_refilled"
    WS_POOL_WARMUP_STARTED = "ws_pool_warmup_started"
    WS_REDIRECT_RECEIVED = "ws_redirect_received"
    WS_SESSION_CLOSED = "ws_session_closed"


_PUBLIC_ACTIONS = {
    LogAction.APP_STARTED,
    LogAction.APP_UI_RUNNING,
    LogAction.APP_UI_EXITED,
    LogAction.APP_EXIT_REQUESTED,
    LogAction.CONFIG_LOADED,
    LogAction.CONFIG_SAVED,
    LogAction.LINK_OPEN_REQUESTED,
    LogAction.LINK_COPY_REQUESTED,
    LogAction.OPEN_LOGS_REQUESTED,
    LogAction.PROXY_STARTING,
    LogAction.PROXY_START_SKIPPED,
    LogAction.PROXY_STOPPED,
    LogAction.PROXY_RESTARTING,
    LogAction.PROXY_SERVER_STARTED,
    LogAction.CONNECT_LINK_DD,
    LogAction.CONNECT_LINK_EE,
    LogAction.CONNECT_SECRET_STANDARD,
    LogAction.RUNTIME_STATS,
}


def _action_value(action: LogAction | str) -> str:
    if isinstance(action, LogAction):
        return action.value
    try:
        return LogAction(action).value
    except ValueError as exc:
        raise ValueError(f"Unknown log action: {action!r}") from exc


def _action_stability(action: str) -> EventStability:
    if LogAction(action) in _PUBLIC_ACTIONS:
        return EventStability.PUBLIC
    return EventStability.INTERNAL


def _event_payload(action: LogAction | str, details: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    action_value = _action_value(action)
    payload = {
        "event_version": EVENT_VERSION,
        "stability": _action_stability(action_value).value,
    }
    payload.update(details)
    return action_value, payload


def set_text_event_meta_visible(visible: bool) -> None:
    global _TEXT_SHOW_EVENT_META
    _TEXT_SHOW_EVENT_META = bool(visible)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if value and all(ch.isalnum() or ch in "._:/-@" for ch in value):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=str)


def _format_payload_line(payload: Mapping[str, Any]) -> str:
    core_keys = [k for k in payload.keys() if k not in ("event_version", "stability")]
    core_keys.sort()
    ordered_keys = list(core_keys)
    if _TEXT_SHOW_EVENT_META:
        ordered_keys.extend([k for k in ("stability", "event_version") if k in payload])
    return ", ".join(f"{key}={_format_value(payload[key])}" for key in ordered_keys)


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def sanitize_config_for_log(cfg: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(cfg)
    if "secret" in safe:
        safe["secret"] = mask_secret(str(safe.get("secret", "")))
    return safe


def log_event(logger: logging.Logger, level: int, action: LogAction | str, **details: Any) -> None:
    action_value, payload = _event_payload(action, details)
    if payload:
        detail_line = _format_payload_line(payload)
        logger.log(level, "[%s] %s", action_value, detail_line)
        return
    logger.log(level, "[%s]", action_value)


def log_exception_event(logger: logging.Logger, action: LogAction | str, **details: Any) -> None:
    action_value, payload = _event_payload(action, details)
    if payload:
        detail_line = _format_payload_line(payload)
        logger.exception("[%s] %s", action_value, detail_line)
        return
    logger.exception("[%s]", action_value)


class EventJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def build_event_formatter(*, json_mode: bool, datefmt: str, text_fmt: str) -> logging.Formatter:
    if json_mode:
        return EventJsonFormatter(datefmt=datefmt)
    return logging.Formatter(text_fmt, datefmt=datefmt)
