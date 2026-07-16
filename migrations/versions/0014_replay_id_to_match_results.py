"""replay_id를 matches에서 match_results로 옮긴다(요청) — 리플레이 메타데이터(맵/시작
시각/경기시간)가 이미 "실제로 어떻게 끝났는가"에 속하는 정보라 match_results에 있는 것과
같은 원칙. 기존 값은 그대로 보존한다(match_id로 이어붙여 데이터 마이그레이션 수행).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("match_results", sa.Column("replay_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_match_results_replay_id", "match_results", "replays", ["replay_id"], ["id"]
    )
    op.create_unique_constraint("uq_match_results_replay_id", "match_results", ["replay_id"])

    op.execute(
        """
        UPDATE match_results
        SET replay_id = matches.replay_id
        FROM matches
        WHERE matches.id = match_results.match_id AND matches.replay_id IS NOT NULL
        """
    )

    op.drop_constraint("uq_matches_replay_id", "matches", type_="unique")
    op.drop_constraint("fk_matches_replay_id", "matches", type_="foreignkey")
    op.drop_column("matches", "replay_id")


def downgrade() -> None:
    op.add_column("matches", sa.Column("replay_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_matches_replay_id", "matches", "replays", ["replay_id"], ["id"])
    op.create_unique_constraint("uq_matches_replay_id", "matches", ["replay_id"])

    op.execute(
        """
        UPDATE matches
        SET replay_id = match_results.replay_id
        FROM match_results
        WHERE match_results.match_id = matches.id AND match_results.replay_id IS NOT NULL
        """
    )

    op.drop_constraint("uq_match_results_replay_id", "match_results", type_="unique")
    op.drop_constraint("fk_match_results_replay_id", "match_results", type_="foreignkey")
    op.drop_column("match_results", "replay_id")
