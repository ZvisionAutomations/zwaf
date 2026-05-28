"""Critical error notification hooks for ZWAF."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from types import TracebackType

logger = logging.getLogger("zwaf.reporting.error_handler")

_ORIGINAL_EXCEPTHOOK = sys.excepthook
_INSTALLED = False


async def notify_critical_error(whatsapp_tool, exc: BaseException) -> None:
    """Notifica erro critico via WhatsApp sem expor stack trace na mensagem."""
    destination = os.getenv("CAIO_PERSONAL_WA_NUMBER", "")
    if not destination:
        logger.warning("critical_error_wa_destination_not_configured")
        return
    if whatsapp_tool is None or not hasattr(whatsapp_tool, "send_message"):
        logger.warning("critical_error_whatsapp_tool_not_configured")
        return

    message = f"ERRO CRITICO ZWAF\n{type(exc).__name__}: {exc}"
    try:
        await whatsapp_tool.send_message(
            phone=destination,
            text=message,
            session_id="critical_error",
        )
    except Exception as notify_exc:
        logger.warning("critical_error_notification_failed: %s", notify_exc)


def _run_notification_sync(whatsapp_tool, exc: BaseException) -> None:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.run(notify_critical_error(whatsapp_tool, exc))
        return

    if loop.is_running():
        loop.create_task(notify_critical_error(whatsapp_tool, exc))
        return

    loop.run_until_complete(notify_critical_error(whatsapp_tool, exc))


def setup_critical_error_handler(whatsapp_tool) -> None:
    """
    Registra handler global para excecoes nao tratadas.

    Nao suprime a excecao original: notifica e delega ao hook/default handler.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    def excepthook(
        exc_type: type[BaseException],
        exc: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        _run_notification_sync(whatsapp_tool, exc)
        _ORIGINAL_EXCEPTHOOK(exc_type, exc, traceback)

    sys.excepthook = excepthook

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        previous_handler = loop.get_exception_handler()

        def loop_exception_handler(event_loop, context):
            exc = context.get("exception")
            if exc is None:
                exc = RuntimeError(str(context.get("message", "Unhandled asyncio exception")))
            event_loop.create_task(notify_critical_error(whatsapp_tool, exc))
            if previous_handler is not None:
                previous_handler(event_loop, context)
            else:
                event_loop.default_exception_handler(context)

        loop.set_exception_handler(loop_exception_handler)

    _INSTALLED = True
    logger.info("critical_error_handler_installed")
