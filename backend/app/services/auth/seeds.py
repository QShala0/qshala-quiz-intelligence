import logging
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.auth import AuthTeam, AuthUser
from backend.app.services.auth.passwords import hash_password

logger = logging.getLogger(__name__)

SEEDED_TEAMS = ["School", "Corporate", "QShala", "Consultant"]


def seed_auth(db: Session) -> None:
    """Idempotently seed the four teams, initial Microsoft admins, and initial consultant user.
    Never overwrites existing rows or manual edits."""
    now = datetime.now(timezone.utc)

    # 1. Seed teams
    for team_name in SEEDED_TEAMS:
        existing_team = db.query(AuthTeam).filter(AuthTeam.id == team_name).first()
        if not existing_team:
            db.add(AuthTeam(id=team_name, name=team_name, created_at=now))
    db.flush()

    # 2. Seed initial Microsoft admins
    raw_admins = settings.AUTH_INITIAL_ADMINS or ""
    for entry in raw_admins.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        email, team = entry.split(":", 1)
        email = email.strip().lower()
        team = team.strip()

        existing_user = db.query(AuthUser).filter(AuthUser.email == email).first()
        if not existing_user:
            display_name = email.split("@")[0].capitalize()
            admin_user = AuthUser(
                id=str(uuid.uuid4()),
                email=email,
                username=None,
                display_name=display_name,
                team_id=team,
                role="admin",
                status="active",
                provider="microsoft",
                password_hash=None,
                ms_oid=None,
                must_change_password=False,
                failed_logins=0,
                locked_until=None,
                last_login_at=None,
                created_at=now,
                created_by="system_seed",
            )
            db.add(admin_user)
            logger.info(f"Seeded initial Microsoft admin: {email} ({team})")

    # 3. Seed consultant password user
    existing_consultant = (
        db.query(AuthUser).filter(AuthUser.username == "user").first()
    )
    if not existing_consultant:
        seed_pw = settings.AUTH_SEED_CONSULTANT_PASSWORD or "pass123"
        consultant_user = AuthUser(
            id=str(uuid.uuid4()),
            email=None,
            username="user",
            display_name="Consultant",
            team_id="Consultant",
            role="member",
            status="active",
            provider="password",
            password_hash=hash_password(seed_pw),
            ms_oid=None,
            must_change_password=False,
            failed_logins=0,
            locked_until=None,
            last_login_at=None,
            created_at=now,
            created_by="system_seed",
        )
        db.add(consultant_user)
        logger.info("Seeded initial consultant password user: user")

    db.commit()
