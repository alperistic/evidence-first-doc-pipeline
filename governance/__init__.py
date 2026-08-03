"""
governance – LexGuard Compliance Layer.

Single Source of Truth für Redaction, Audit, Policy und Dokumentklassen-Logik.
Alle anderen Module importieren aus diesem Paket.

Öffentliche API:
  from governance.redaction import prepare_for_llm, restore_from_llm, ...
  from governance.audit import log_request, get_recent_logs
  from governance.policy import load_policy, LoadedPolicy, PolicyConfig
  from governance.doc_class_policy import normalize_doc_class, resolve_display_document_type, ...
"""
