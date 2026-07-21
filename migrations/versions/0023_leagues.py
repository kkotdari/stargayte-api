"""공식 리그(토너먼트) 대진/결과 관리 — 팀리그/개인리그 구분, A~F 고정 팀, 로스터,
단일 엘리미네이션 대진표, 세트 스코어 결과, 대타(1회성) 5개 테이블을 새로 만든다
(요청: "다음 버전에서 오픈할 리그 페이지" — 일단 운영자만 볼 수 있게).

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leagues",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="team"),
        sa.Column("best_of", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("draw_size", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("best_of >= 1", name="ck_leagues_best_of_positive"),
        sa.CheckConstraint("mode IN ('team', 'individual')", name="ck_leagues_mode_valid"),
    )

    op.create_table(
        "league_teams",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("league_id", sa.BigInteger(), sa.ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=1), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("league_id", "label", name="uq_league_teams_league_label"),
    )

    op.create_table(
        "league_team_members",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("league_id", sa.BigInteger(), sa.ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("league_team_id", sa.BigInteger(), sa.ForeignKey("league_teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_pk", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.UniqueConstraint("league_team_id", "position", name="uq_league_team_members_team_position"),
        # "같은 리그 안에서 한 선수는 팀 하나에만" — DB 레벨 강제(요청 6).
        sa.UniqueConstraint("league_id", "member_pk", name="uq_league_team_members_league_member"),
    )

    op.create_table(
        "league_matches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("league_id", sa.BigInteger(), sa.ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round", sa.SmallInteger(), nullable=False),
        sa.Column("slot_in_round", sa.SmallInteger(), nullable=False),
        sa.Column("team_a_id", sa.BigInteger(), sa.ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("team_b_id", sa.BigInteger(), sa.ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_dead", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sets_won_a", sa.SmallInteger(), nullable=True),
        sa.Column("sets_won_b", sa.SmallInteger(), nullable=True),
        sa.Column("winner_team_id", sa.BigInteger(), sa.ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("result_entered_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("result_entered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("league_id", "round", "slot_in_round", name="uq_league_matches_league_round_slot"),
        sa.CheckConstraint("round >= 1", name="ck_league_matches_round_positive"),
        sa.CheckConstraint("slot_in_round >= 0", name="ck_league_matches_slot_nonneg"),
    )

    op.create_table(
        "league_match_substitutions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("league_match_id", sa.BigInteger(), sa.ForeignKey("league_matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("league_teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("roster_position", sa.SmallInteger(), nullable=False),
        sa.Column("substitute_member_pk", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "league_match_id", "team_id", "roster_position",
            name="uq_league_match_substitutions_match_team_position",
        ),
    )


def downgrade() -> None:
    op.drop_table("league_match_substitutions")
    op.drop_table("league_matches")
    op.drop_table("league_team_members")
    op.drop_table("league_teams")
    op.drop_table("leagues")
