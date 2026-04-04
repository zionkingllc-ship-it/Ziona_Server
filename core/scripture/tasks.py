import logging

from celery import shared_task

from core.scripture.importer import ScriptureImporter

logger = logging.getLogger("core.scripture")


@shared_task
def import_bible_async(
    translations: list[str], batch_size: int = 1000, resume: bool = False
) -> str:
    """Async wrapper for the production-grade ScriptureImporter pipeline.

    Args:
        translations: List of lowercase translation codes (e.g., ['kjv', 'asv']).
        batch_size: Number of verses to bulk create at once.
        resume: Whether to resume gracefully.
    """
    logger.info(
        f" Celery Worker starting async Bible import for: {translations} "
        f"(batch_size={batch_size}, resume={resume})"
    )

    try:
        importer = ScriptureImporter(batch_size=batch_size, resume=resume)
        importer.run_import(translations)

        success_msg = f"Successfully imported translations: {', '.join(translations)}"
        logger.info(success_msg)
        return success_msg
    except Exception as e:
        logger.error(f"Async Bible import failed catastrophically: {e}", exc_info=True)
        raise
