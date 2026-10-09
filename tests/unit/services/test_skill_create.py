from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services.skill import SkillService


def test_duplicate_create_keeps_existing_skill(tmp_path: Path) -> None:
    service = SkillService(str(tmp_path))
    service.create_skill("my-skill", "original", "original body")
    with pytest.raises(FileExistsError):
        service.create_skill("My Skill", "changed", "changed body")
    assert service.get_skill("my-skill")["content"] == "original body"


def test_concurrent_creation_across_stores_has_one_winner(tmp_path: Path) -> None:
    services = [SkillService(str(tmp_path)), SkillService(str(tmp_path))]

    def create(service: SkillService) -> bool:
        try:
            service.create_skill("same", "description", "body")
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, services)) == [False, True]
