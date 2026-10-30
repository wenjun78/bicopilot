"""Anonymous page-visit analytics for the private BI Copilot owner dashboard.

Only a salted browser identifier is stored, not raw IP addresses. This is
best-effort page-load tracking, not identity tracking or a substitute for
server-side monitoring. All timestamps are naive UTC for SQLite/Postgres.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, DateTime, Index, MetaData, String, Table, func, insert, select

metadata = MetaData()
visits = Table(
    "copilot_visits",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("created_at", DateTime, nullable=False),
    Column("visitor_hash", String(24), nullable=False),
    Column("session_id", String(64), nullable=False),
    Column("path", String(160), nullable=False),
    Column("referrer", String(200), nullable=False, default="direct"),
    Index("ix_copilot_visits_created", "created_at"),
    Index("ix_copilot_visits_visitor", "visitor_hash"),
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SiteAnalytics:
    def __init__(self, engine, secret_salt: str, runs_table):
        self.engine = engine
        self.secret_salt = secret_salt
        self.runs = runs_table
        metadata.create_all(engine)

    def record_visit(self, visitor_id: str, session_id: str, path: str, referrer: str) -> None:
        # Browser ID is never stored; hashes are stable only while SECRET_SALT stays unchanged.
        visitor_hash = hashlib.sha256(f"{self.secret_salt}:{visitor_id}".encode()).hexdigest()[:24]
        with self.engine.begin() as conn:
            conn.execute(insert(visits).values(
                    id=str(uuid.uuid4()),
                    created_at=_utcnow(),
                    visitor_hash=visitor_hash,
                    session_id=session_id[:64],
                    path=path[:160],
                    referrer=referrer[:200],
            ))

    def summary(self, days: int = 30) -> dict:
        now = _utcnow()
        start = datetime.combine((now - timedelta(days=days - 1)).date(), datetime.min.time())
        r = self.runs
        with self.engine.connect() as conn:
            total_visits = conn.execute(
                select(func.count()).select_from(visits).where(visits.c.created_at >= start)
            ).scalar_one()
            unique_visitors = conn.execute(
                select(func.count(func.distinct(visits.c.visitor_hash))).where(visits.c.created_at >= start)
            ).scalar_one()
            total_questions = conn.execute(
                select(func.count()).select_from(r).where(r.c.created_at >= start)
            ).scalar_one()
            answered = conn.execute(
                select(func.count()).select_from(r).where(r.c.created_at >= start, r.c.status == "answered")
            ).scalar_one()
            avg_ms = conn.execute(
                select(func.avg(r.c.total_ms)).where(r.c.created_at >= start, r.c.total_ms.is_not(None))
            ).scalar_one()
            cached = conn.execute(
                select(func.count()).select_from(r).where(r.c.created_at >= start, r.c.cached.is_(True))
            ).scalar_one()
            sessions = conn.execute(
                select(func.count(func.distinct(r.c.session_id))).where(
                    r.c.created_at >= start, r.c.session_id.is_not(None), r.c.session_id != ""
                )
            ).scalar_one()
            visit_days = conn.execute(
                select(func.date(visits.c.created_at), func.count(), func.count(func.distinct(visits.c.visitor_hash)))
                .where(visits.c.created_at >= start)
                .group_by(func.date(visits.c.created_at))
            ).all()
            question_days = conn.execute(
                select(func.date(r.c.created_at), func.count())
                .where(r.c.created_at >= start)
                .group_by(func.date(r.c.created_at))
            ).all()
            referrals = conn.execute(
                select(visits.c.referrer, func.count())
                .where(visits.c.created_at >= start)
                .group_by(visits.c.referrer)
                .order_by(func.count().desc())
                .limit(6)
            ).all()
            statuses = conn.execute(
                select(r.c.status, func.count())
                .where(r.c.created_at >= start)
                .group_by(r.c.status)
                .order_by(func.count().desc())
            ).all()
            latest = (
                conn.execute(
                    select(
                        visits.c.created_at,
                        visits.c.visitor_hash,
                        visits.c.session_id,
                        visits.c.path,
                        visits.c.referrer,
                    )
                    .order_by(visits.c.created_at.desc())
                    .limit(25)
                )
                .mappings()
                .all()
            )

        vdays = {str(day)[:10]: (int(n), int(uniq)) for day, n, uniq in visit_days}
        qdays = {str(day)[:10]: int(n) for day, n in question_days}
        daily = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
            n, uniq = vdays.get(day, (0, 0))
            daily.append({"day": day, "visits": n, "visitors": uniq, "questions": qdays.get(day, 0)})

        return {
            "days": days,
            "timezone": "UTC",
            "visits": int(total_visits),
            "unique_visitors": int(unique_visitors),
            "questions": int(total_questions),
            "answered": int(answered),
            "question_sessions": int(sessions),
            "avg_ms": round(float(avg_ms or 0)),
            "cached": int(cached),
            "daily": daily,
            "referrers": [{"name": label or "direct", "count": int(n)} for label, n in referrals],
            "statuses": [{"name": label or "unknown", "count": int(n)} for label, n in statuses],
            "recent_visits": [
                {
                    "created_at": row["created_at"].isoformat() + "Z",
                    "visitor": row["visitor_hash"][:12],
                    "session_id": row["session_id"],
                    "path": row["path"],
                    "referrer": row["referrer"],
                }
                for row in latest
            ],
        }
