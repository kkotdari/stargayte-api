from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 ORM 모델의 공통 베이스.

    eager_defaults=True: onupdate가 있는 컬럼(예: updated_at)은 INSERT 직후 서버 생성값이
    바로 로드되지 않고 "다음 UPDATE 때 채워짐" 상태로 남는다. 커밋 후 그 값을 바로
    직렬화(MemberOut 등)해야 하는 이 프로젝트 구조상, 매번 명시적으로 refresh하는 대신
    전역으로 커밋 시점에 즉시 채워지도록 한다.
    """

    __mapper_args__ = {"eager_defaults": True}
