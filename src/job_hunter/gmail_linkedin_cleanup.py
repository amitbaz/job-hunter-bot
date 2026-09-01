from __future__ import annotations


def _job_has_dependencies(conn, job_id: int) -> bool:
    for table in ("evaluations", "materials", "deliveries", "application_events"):
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE job_id = ? LIMIT 1",
            (job_id,),
        ).fetchone():
            return True
    return False


def release_legacy_blank_linkedin_jobs(store) -> int:
    """Release only safe blank LinkedIn Gmail artifacts for reprocessing.

    Older deterministic LinkedIn JOB_ALERT handling staged URL-only candidates and
    materialized blank jobs. A message is released only when all of its LinkedIn
    candidates are blank and every matching gmail:linkedin job is also blank and
    has no dependent evaluation, material, delivery, or application event.
    """

    conn = store._conn
    candidate_messages = conn.execute(
        """
        SELECT DISTINCT m.message_id
        FROM gmail_messages m
        JOIN inbound_job_candidates c
          ON c.source_message_id = m.message_id
        WHERE m.classification = 'JOB_ALERT'
          AND c.origin = 'gmail'
          AND lower(c.source_platform) = 'linkedin'
          AND trim(c.company) = ''
          AND trim(c.title) = ''
          AND NOT EXISTS (
              SELECT 1
              FROM inbound_job_candidates populated
              WHERE populated.source_message_id = m.message_id
                AND populated.origin = 'gmail'
                AND lower(populated.source_platform) = 'linkedin'
                AND (
                    trim(populated.company) <> ''
                    OR trim(populated.title) <> ''
                )
          )
        """
    ).fetchall()

    released = 0
    with conn:
        for message_row in candidate_messages:
            message_id = message_row["message_id"]
            candidates = conn.execute(
                """
                SELECT id, source_candidate_key
                FROM inbound_job_candidates
                WHERE source_message_id = ?
                  AND origin = 'gmail'
                  AND lower(source_platform) = 'linkedin'
                  AND trim(company) = ''
                  AND trim(title) = ''
                """,
                (message_id,),
            ).fetchall()

            job_ids: list[int] = []
            safe = True
            for candidate in candidates:
                jobs = conn.execute(
                    """
                    SELECT id, company, title
                    FROM jobs
                    WHERE source = 'gmail:linkedin'
                      AND source_job_id = ?
                    """,
                    (candidate["source_candidate_key"],),
                ).fetchall()
                for job in jobs:
                    if job["company"].strip() or job["title"].strip():
                        safe = False
                        break
                    if _job_has_dependencies(conn, job["id"]):
                        safe = False
                        break
                    job_ids.append(job["id"])
                if not safe:
                    break

            if not safe:
                continue

            conn.execute(
                """
                DELETE FROM inbound_job_candidates
                WHERE source_message_id = ?
                  AND origin = 'gmail'
                  AND lower(source_platform) = 'linkedin'
                  AND trim(company) = ''
                  AND trim(title) = ''
                """,
                (message_id,),
            )
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                conn.execute(
                    f"DELETE FROM jobs WHERE id IN ({placeholders})",
                    job_ids,
                )
            conn.execute(
                """
                DELETE FROM gmail_messages
                WHERE message_id = ?
                  AND classification = 'JOB_ALERT'
                """,
                (message_id,),
            )
            released += 1

    return released
