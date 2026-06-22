"""武器公共能力。

二级组件如果需要“发初始武器、读取当前武器、生成掉落武器”，统一调用这里。
这样探险、战斗、武器组件之间不会互相引用。
"""

from __future__ import annotations

from .common import CoreService, dump_json, enchant_label_name, random, random_quality, ts, weapon_id_label
from .sql import db


class WeaponCore(CoreService):
    """武器实例的基础读写能力。"""

    def ensure_starter_weapon(self, client_id: str) -> None:
        """玩家没有武器时，补一把默认短剑。"""

        self.ensure_player_weapon(client_id)

    def create_drop_weapon(self, client_id: str, location_name: str = "") -> str:
        """直接创建一把掉落武器，并返回展示文本。"""

        drop = self.roll_weapon_drop(location_name)
        weapon_id = self.create_weapon(
            client_id,
            drop["weapon_def_id"],
            drop["quality"],
            drop["max_level"],
            equipped=False,
        )
        return f"{weapon_id_label(weapon_id)} {drop['name']}[{drop['quality']}] 上限{drop['max_level']}"

    def roll_weapon_drop(self, location_name: str = "") -> dict:
        """只随机出掉落结果，不写入数据库。

        探险预计算阶段会先记录掉落结果，等玩家发送“结束探险”时再真正发放。
        传入地点时优先从该地点武器里抽；不传地点时从全部武器里抽。
        """

        rows = self.db.fetch_all("SELECT * FROM weapon_defs")
        if location_name:
            same_location = [row for row in rows if row["drop_location"] == location_name]
            rows = same_location or rows
        weapon_def = random.choice(rows)
        return {
            "weapon_def_id": weapon_def["weapon_def_id"],
            "name": weapon_def["name"],
            "quality": random_quality(),
            "max_level": self.random_max_level(),
        }

    def create_weapon(
        self,
        client_id: str,
        weapon_def_id: str,
        quality: str,
        max_level: int,
        equipped: bool = False,
    ) -> int:
        """创建一把玩家真实拥有的武器。"""

        with self.db.transaction() as conn:
            return self.create_weapon_conn(
                conn,
                client_id,
                weapon_def_id,
                quality,
                max_level,
                equipped=equipped,
            )

    def create_weapon_conn(
        self,
        conn,
        client_id: str,
        weapon_def_id: str,
        quality: str,
        max_level: int,
        equipped: bool = False,
    ) -> int:
        """在事务里创建一把玩家真实拥有的武器。"""

        weapon_def = self.db.fetch_one("SELECT * FROM weapon_defs WHERE weapon_def_id = ?", (weapon_def_id,))
        if not weapon_def:
            raise ValueError("武器定义不存在")
        if equipped:
            conn.execute("UPDATE player_weapons SET equipped = 0 WHERE holder_id = ?", (client_id,))
        cursor = conn.execute(
            """
            INSERT INTO player_weapons
            (holder_id, weapon_def_id, level, max_level, quality, equipped, enchant_effects, custom_name, created_at)
            VALUES (?, ?, 0, ?, ?, ?, ?, '', ?)
            """,
            (
                client_id,
                weapon_def_id,
                max_level,
                quality,
                1 if equipped else 0,
                dump_json([]),
                ts(),
            ),
        )
        weapon_id = int(cursor.lastrowid)
        self.record_weapon_created_conn(conn, client_id, weapon_id)
        return weapon_id

    def equipped_weapon(self, client_id: str) -> dict | None:
        """读取玩家当前装备的武器。"""

        self.ensure_starter_weapon(client_id)
        return self.db.fetch_one(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id = ? AND w.equipped = 1
            LIMIT 1
            """,
            (client_id,),
        )

    def weapons(self, client_id: str) -> list[dict]:
        """读取玩家全部武器。"""

        return self.db.fetch_all(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id = ?
            ORDER BY w.equipped DESC, w.weapon_id
            """,
            (client_id,),
        )

    def weapon(self, client_id: str, weapon_id: int) -> dict | None:
        """读取玩家某一把武器。"""

        return self.db.fetch_one(
            """
            SELECT w.*, d.name, d.drop_location, d.base_attack, d.skill_id, d.weapon_type
            FROM player_weapons w
            JOIN weapon_defs d ON d.weapon_def_id = w.weapon_def_id
            WHERE w.holder_id = ? AND w.weapon_id = ?
            """,
            (client_id, weapon_id),
        )

    def skill(self, skill_id: str) -> dict:
        """读取武器自带技能；缺失时给一个普通攻击兜底。"""

        return self.db.fetch_one("SELECT * FROM weapon_skill_defs WHERE skill_id = ?", (skill_id,)) or {
            "skill_id": "",
            "name": "普通攻击",
            "cost_mp": 0,
            "interval": 99,
            "power": 1.0,
        }

    def weapon_skill_label(self, weapon_id: int, skill: dict) -> str:
        """读取某把武器的自带技能显示名。

        `weapon_enchant_names.slot_no = 0` 专门表示自带技能铭刻名。
        `slot_no = 1..n` 继续表示已附魔技能书的铭刻名，所以不需要新增表。
        """

        base_name = str(skill.get("name", "普通攻击"))
        row = self.db.fetch_one(
            "SELECT custom_name FROM weapon_enchant_names WHERE weapon_id = ? AND slot_no = 0",
            (weapon_id,),
        )
        return enchant_label_name(base_name, row["custom_name"] if row else "")

    @staticmethod
    def random_max_level() -> int:
        """加权随机武器等级上限，不受玩家等级影响。"""

        band = random.choices(
            ("20-40", "41-60", "61-80", "81-90", "91-99", "100"),
            weights=(45, 28, 18, 6, 2.5, 0.5),
            k=1,
        )[0]
        if band == "20-40":
            return random.randint(20, 40)
        if band == "41-60":
            return random.randint(41, 60)
        if band == "61-80":
            return random.randint(61, 80)
        if band == "81-90":
            return random.randint(81, 90)
        if band == "91-99":
            return random.randint(91, 99)
        return 100


service = WeaponCore(db)

__all__ = ["WeaponCore", "service"]
