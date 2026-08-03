-- Count LLM calls for the latest analysis run.
-- Primary scope: output_json.analysis_run_id
-- Fallback scope: same doc_hash within +/-10 seconds around latest row (legacy rows without run_id).
WITH latest AS (
    SELECT
        id,
        timestamp,
        doc_hash,
        json_extract(output_json, '$.analysis_run_id') AS run_id
    FROM audit_log
    ORDER BY id DESC
    LIMIT 1
),
scoped AS (
    SELECT *
    FROM audit_log
    WHERE (
        (SELECT run_id FROM latest) IS NOT NULL
        AND json_extract(output_json, '$.analysis_run_id') = (SELECT run_id FROM latest)
    ) OR (
        (SELECT run_id FROM latest) IS NULL
        AND doc_hash = (SELECT doc_hash FROM latest)
        AND ABS(
            (julianday(timestamp) - julianday((SELECT timestamp FROM latest))) * 86400.0
        ) <= 10
    )
)
SELECT
    (SELECT run_id FROM latest) AS analysis_run_id,
    COUNT(*) AS llm_calls,
    COUNT(DISTINCT doc_class) AS doc_classes_seen,
    SUM(COALESCE(total_tokens, 0)) AS total_tokens
FROM scoped;

-- Breakdown for the same run by audit doc_class (segmenter source-of-truth).
WITH latest AS (
    SELECT
        id,
        timestamp,
        doc_hash,
        json_extract(output_json, '$.analysis_run_id') AS run_id
    FROM audit_log
    ORDER BY id DESC
    LIMIT 1
),
scoped AS (
    SELECT *
    FROM audit_log
    WHERE (
        (SELECT run_id FROM latest) IS NOT NULL
        AND json_extract(output_json, '$.analysis_run_id') = (SELECT run_id FROM latest)
    ) OR (
        (SELECT run_id FROM latest) IS NULL
        AND doc_hash = (SELECT doc_hash FROM latest)
        AND ABS(
            (julianday(timestamp) - julianday((SELECT timestamp FROM latest))) * 86400.0
        ) <= 10
    )
)
SELECT
    doc_class,
    COUNT(*) AS calls,
    SUM(COALESCE(total_tokens, 0)) AS total_tokens
FROM scoped
GROUP BY doc_class
ORDER BY calls DESC, doc_class ASC;
