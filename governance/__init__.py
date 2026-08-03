"""
governance – LexGuard governance layer.

Single source of truth for redaction, audit, policy, and document-class logic.
All other modules import from this package.

Public API:
  from governance.redaction import prepare_for_llm, restore_from_llm, ...
  from governance.audit import log_request, get_recent_logs
  from governance.policy import load_policy, LoadedPolicy, PolicyConfig
  from governance.doc_class_policy import normalize_doc_class, resolve_display_document_type, ...
"""
