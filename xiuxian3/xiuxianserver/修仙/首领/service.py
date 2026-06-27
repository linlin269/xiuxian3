"""岁时情劫首领服务。

岁时情劫按每日 04:00 的业务日刷新。
节气和传统节日命中时，玩家发送首领相关命令才会懒加载生成当日首领。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import floor, sqrt
from statistics import median
from typing import Any

from lunardate import LunarDate

from .. import combat_log_text
from ..combat_core import service as combat_service
from ..common import CoreService, dt, dump_json, load_json, money, now, random, ts, weapon_id_label
from ..constants import (
    DAY_RESET_HOUR,
    MAX_LEVEL,
    SEASONAL_BOSS_CHALLENGE_COOLDOWN_MINUTES,
    SEASONAL_BOSS_MAX_CHALLENGES,
)
from ..format_text import T
from ..rules import damage_after_defense, monster_exp
from ..sect_war import record_sect_merit_conn, sect_direction_bonus_conn
from ..sql import db
from ..weapon_core import service as weapon_service
from ..异火.service import service as flame_service
from ..镇渊诛邪.service import service as zhenyuan_zhuxie_service


SEASONAL_LOW_CONTRIBUTION_FLOORS = {
    "feather": 0.025,
    "material": 0.015,
    "gem": 0.080,
    "book": 0.030,
    "weapon": 0.015,
}
SEASONAL_FEATHER_SCORE_BONUS = 0.018
SEASONAL_FEATHER_CHANCE_CAP = 0.22


@dataclass(frozen=True)
class BossDef:
    """一只岁时情劫的文字和基础配置。"""

    key: str
    name: str
    title: str
    scene: str
    story: str
    atmosphere: tuple[str, str, str]
    farewell: str
    feather_text: str
    location: str


def _boss(
    key: str,
    name: str,
    title: str,
    scene: str,
    story: str,
    atmosphere: tuple[str, str, str],
    farewell: str,
    feather_text: str,
    location: str,
) -> BossDef:
    """让下面的大段定义更短一些。"""

    return BossDef(key, name, title, scene, story, atmosphere, farewell, feather_text, location)


SOLAR_TERM_RULES = {
    "xiaohan": (1, 5.4055),
    "dahan": (1, 20.12),
    "lichun": (2, 3.87),
    "yushui": (2, 18.73),
    "jingzhe": (3, 5.63),
    "chunfen": (3, 20.646),
    "qingming": (4, 4.81),
    "guyu": (4, 20.1),
    "lixia": (5, 5.52),
    "xiaoman": (5, 21.04),
    "mangzhong": (6, 5.678),
    "xiazhi": (6, 21.37),
    "xiaoshu": (7, 7.108),
    "dashu": (7, 22.83),
    "liqiu": (8, 7.5),
    "chushu": (8, 23.13),
    "bailu": (9, 7.646),
    "qiufen": (9, 23.042),
    "hanlu": (10, 8.318),
    "shuangjiang": (10, 23.438),
    "lidong": (11, 7.438),
    "xiaoxue": (11, 22.36),
    "daxue": (12, 7.18),
    "dongzhi": (12, 21.94),
}


def _solar_term_day(year: int, month: int, constant: float) -> int:
    """用 21 世纪节气公式计算某年某节气在公历几号。"""

    century_year = year % 100
    if month <= 2:
        leap_count = max(0, (century_year - 1) // 4)
    else:
        leap_count = century_year // 4
    return int(century_year * 0.2422 + constant) - leap_count


def _solar_term_key(value: date) -> str:
    """判断当天是否命中二十四节气。"""

    for key, (month, constant) in SOLAR_TERM_RULES.items():
        if value.month == month and value.day == _solar_term_day(value.year, month, constant):
            return key
    return ""


LUNAR_FESTIVAL_DATES = {
    (1, 1): "chunjie",
    (1, 15): "yuanxiao",
    (2, 2): "longtaitou",
    (2, 12): "huazhao",
    (3, 3): "shangsi",
    (5, 5): "duanwu",
    (7, 7): "qixi",
    (7, 15): "zhongyuan",
    (8, 15): "zhongqiu",
    (9, 9): "chongyang",
    (10, 1): "hanyi",
    (10, 15): "xiayuan",
    (12, 8): "laba",
    (12, 23): "xiaonian",
}


HIGH_WEIGHT_FESTIVALS = {"chunjie", "yuanxiao", "duanwu", "qixi", "zhongqiu", "chongyang"}


BOSS_DEFS: dict[str, BossDef] = {
    "lichun": _boss(
        "lichun",
        "折柳青郎",
        "渡口送别的少年",
        "立春这日，渡口老柳先于百草发青。一个青衫少年站在柳下，指间折着新枝，像还在等一艘迟迟不归的船。",
        "他曾在立春送心爱的姑娘远行，折柳相赠，说等柳枝再青，她便该回来了。后来春风一年年吹过渡口，船却再没有靠岸。",
        ("柳絮还未飞起，渡口却像下了一场细雪。", "他抬眼看你，眼神干净得像还不知道结局。", "水声拍岸，像有一艘船永远差一点靠近。"),
        "折柳青郎把柳枝放进水里，任它漂向远方。",
        "水面浮起一枚淡青色的铭刻之羽，带着新柳和冷茶的气息。像一句少年没能亲口说出的：我一直在这里。",
        "旧渡口",
    ),
    "yushui": _boss(
        "yushui",
        "听雨灯娘",
        "窗前守灯的少女",
        "雨水入夜，旧巷尽头亮起一盏小灯。窗纸上映出少女侧影，她用手护着灯火，像怕风雨吹灭某人的归路。",
        "绣坊少女为远行人留灯，等第一场春雨后归舟靠岸。可布船沉在无人知晓的江湾，她却夜夜剪灯，夜夜听雨。",
        ("雨水顺着屋檐滴落，像一针一线缝进夜里。", "灯火照着她的脸，一半是少女，一半是旧雨。", "她怀里的绣帕没有绣完，并蒂莲只开了一朵。"),
        "灯娘轻轻吹熄灯火，窗纸上的影子终于淡去。",
        "灯盏旁落下一枚微湿的铭刻之羽，羽尖带着一点灯油香。像有人把一夜没敢熄灭的等待，交到了你手里。",
        "旧巷",
    ),
    "jingzhe": _boss(
        "jingzhe",
        "惊雷梦龙郎",
        "梦里养龙的少年",
        "惊蛰春雷滚过雷泽，泥水翻起细小银光。一个赤脚少年从雷声里醒来，身后拖着半条由梦化成的龙影。",
        "少年曾在梦里养出一缕小龙，答应惊蛰雷响后带它越过雷泽。可少年死在春雷之前，小龙便年年撞碎梦境来寻他。",
        ("雷光照亮他的脸，稚气未退，却满眼认真。", "龙影绕过你的兵刃，像一场还没醒完的梦。", "泥水映出的不是雷泽，而是病榻和旧窗。"),
        "少年摸了摸身后的龙影，轻声说：再等等，明年我们一定走。",
        "雷泽泥水中浮出一枚带细雷纹的铭刻之羽。羽上微微发热，像少年掌心捧过的一点梦。",
        "雷泽",
    ),
    "chunfen": _boss(
        "chunfen",
        "半影镜姑娘",
        "抱着半面镜的少女",
        "春分日，昼夜正平。日光照进废园时，一面断镜忽然映出少女的影子，她抱着镜子，像抱着一个再也拼不回的人。",
        "她与远行少年分镜为约，说归来时两半合圆。后来少年死在京中风雪里，另一半镜子再也没有回来。",
        ("她举起断镜，你的影子被照得只剩一半。", "园中花影分成明暗两边，谁也不肯越界。", "她问你：若一人不归，另一半誓言还算数吗？"),
        "断镜轻轻一颤，映出的少女影子慢慢模糊。",
        "镜光碎处，一枚半明半暗的铭刻之羽落下。羽面映不出人脸，只映出一场没有合圆的约定。",
        "废园",
    ),
    "qingming": _boss(
        "qingming",
        "纸衣书生",
        "死在归途的少年郎",
        "清明雨落，桥头纸灰贴着水面漂了一夜。一个纸衣书生站在桥边，怀里抱着一封被雨洇开的家书。",
        "少年高中后连夜归乡，想告诉未婚妻自己终于可以娶她。山雨断路，家书未寄，他便年年在桥头等灯。",
        ("纸灰落在你的肩上，竟有一点人的体温。", "他护着怀中家书，像护着世上最后一点喜讯。", "雨水打湿他的纸衣，露出下面早已不存在的少年骨。"),
        "纸衣书生向桥那头作了一揖，身影随纸灰飞远。",
        "桥边落下一枚灰白色铭刻之羽，沾着清明雨和淡淡墨香。像一封终于寄出的迟信。",
        "旧桥",
    ),
    "guyu": _boss(
        "guyu",
        "埋香花娘",
        "山中种花的姑娘",
        "谷雨时节，山田百花同时低头。泥土深处传来姑娘轻轻的歌声，像有人在为一场没有开始的婚礼铺花。",
        "她与采药少年约好谷雨后成亲，少年却为换红绸入山未归。她把红绸、花种和名字一起埋进泥里。",
        ("花香浓得像喜宴，却没有半点人声。", "她赤足踏过泥水，每一步都开出一朵未嫁的花。", "她袖中藏着半截红绸，颜色新得令人难过。"),
        "满山花香淡去，像一场迟迟没有办成的婚礼终于散席。",
        "花泥中浮起一枚浅黄色铭刻之羽，沾着雨后花香和一点红绸碎影。像一声没等到回应的“你回来了”。",
        "花田",
    ),
    "lixia": _boss("lixia", "蝉衣少年", "树下脱不下旧壳的少年魂", "立夏午后，老槐树上响起第一声蝉鸣，薄衣少年披着半透明的蝉蜕醒来。", "他曾约邻家姑娘立夏去河边捉第一只蝉，却病死在蝉鸣之前。后来每年立夏，他都以为自己只是睡过了头。", ("蝉鸣忽近忽远，像从很多年前的树影里传来。", "少年伸手去抓肩上的蝉蜕，却怎么也脱不下来。", "树叶筛下的光斑落在他脸上，像一个未曾长大的夏天。"), "少年跑向河边，半途碎成满树薄翼。", "槐树下落下一枚透明微黄的铭刻之羽，轻得像一枚空蝉壳。像少年迟到多年后，终于听见了第一声夏。", "老槐树"),
    "xiaoman": _boss("xiaoman", "未满酒娘", "倒酒等人的少女", "小满黄昏，酒肆门前的旧酒盏自己斟起半杯。少女按着酒壶，怎么也不肯倒满。", "她等赶路少年回来喝合卺酒，说等他进门再倒最后一线。少年死在外乡荒店，那杯酒便永远未满。", ("酒香很甜，甜得像一场未办成的喜宴。", "她望着门外，眼里盛着半杯将落未落的光。", "酒液轻晃，却始终差一滴才满。"), "她把半杯酒倒在地上，低声说，原来等人也会醉。", "酒盏旁浮起一枚带甜香的铭刻之羽。羽尖湿润，像一杯始终没有倒满的合卺酒。", "旧酒肆"),
    "mangzhong": _boss("mangzhong", "麦信郎", "麦田藏信的少年", "芒种风过，金黄麦浪里传来纸页翻动的声音。布衣少年抱着一束沉甸甸的麦穗。", "他把第一封情书藏进麦穗，想在收麦时送给教书姑娘。战乱烧毁村学，信被风雨磨成碎屑。", ("麦芒划过你的手背，像纸边轻轻割开旧事。", "少年把麦穗抱得很紧，仿佛里面藏着一生勇气。", "风吹过田野，无数碎纸在麦间低语。"), "他展开掌心，只剩一撮金黄麦壳，便把它们撒回田里。", "麦浪中飞出一枚金色铭刻之羽，羽根夹着极细的纸屑。像一封终于敢写、却再没人读的信。", "麦田"),
    "xiazhi": _boss("xiazhi", "长日晒衣女", "晒嫁衣的姑娘", "夏至白昼漫长，村口晒衣绳上挂起一件红嫁衣。少女抬头望着日光。", "她的未婚夫说夏至前必归，因为白昼最长，路也该最好走。边关大火吞没粮队，嫁衣晒到褪色。", ("日光白得刺眼，红嫁衣却像刚从血里洗出。", "她抚平衣角，每一下都像在抚一封迟来的讣告。", "你的影子被拉得很长，长到仿佛能走去边关。"), "她解下嫁衣抱在怀里，夕阳终于落下。", "晒衣绳上飘下一枚艳红的铭刻之羽，带着日晒后的暖意。像一件穿不上的嫁衣，仍替谁保留着体温。", "晒衣场"),
    "xiaoshu": _boss("xiaoshu", "热井藏信郎", "把信藏进井边的少年", "小暑热气蒸腾，荒井边的青石渗出墨痕。少年伏在井沿，寻找那封藏错地方的信。", "他怕雨湿了信，便把心事藏进井边石缝。取信时失足跌落，同窗姑娘一生都不知道这封信。", ("井口热气扑面，像有人把旧信贴在火上烘。", "少年指尖满是青苔和墨迹，眼神却亮得可怜。", "井底有纸张卷曲的声音，像一句话被热气烫弯。"), "他取出一片空白纸灰，忽然笑了，像信从未写过也好。", "井沿浮起一枚带墨痕的铭刻之羽，触手微烫。像一封藏得太深、终于烧空的少年心事。", "荒井"),
    "dashu": _boss("dashu", "焚荷红衣女", "荷塘烧旧约的新娘", "大暑正午，荷塘忽起红火。红衣少女立在火色荷叶间，攥着半张烧焦婚书。", "她被迫另嫁，误以为心上人负约，含泪烧掉婚书投了荷塘。多年后真相才被风传来。", ("红荷一朵朵燃起，却没有半点灰烬。", "她把婚书举向你，声音很轻：你也来晚了吗？", "热浪里有谁拍打祠堂门板，声声都迟了许多年。"), "她像终于听见了那晚没传到的敲门声，泪落进荷心。", "焦黑荷叶上留下一枚红边铭刻之羽，带着烟火和莲香。像一场误会烧到最后，只剩一句来不及解释。", "荷塘"),
    "liqiu": _boss("liqiu", "折簪秋娘", "秋风里拾断簪的少女", "立秋风起，长街尽头滚来半支断簪。素衣少女追着簪影而来，鬓边空空。", "卖剑少年送她木簪作聘，说秋收后换金簪。矿山塌方后，送回来的只有半支断簪。", ("秋风卷过，她鬓边发丝被吹得很乱。", "断簪在地上轻轻转动，像一直指向回不去的山路。", "她问你：若他真的不来，为什么还把簪子带在身上？"), "她把断簪插回发间，簪仍缺一半，她却不再追了。", "秋风中落下一枚木色铭刻之羽，羽骨细长如簪。像一份寒酸却真心的聘礼，断了也舍不得丢。", "长街"),
    "chushu": _boss("chushu", "病酒少年", "夏末醉倒的少年", "处暑暑气将退，酒楼旧席多出一只空坛。少年倚栏醉笑，眼尾却红得像刚哭过。", "他被假信骗走，以为唱曲姑娘心甘情愿嫁入高门。多年后才知她也在同夜被带往远乡。", ("酒气里混着残暑，熏得人眼眶发热。", "他笑得很响，可每次举杯都像在挡泪。", "楼下似有女子唱曲，唱到一半便被风吹散。"), "他放下空杯，轻声说：若是被拦住，那就不怪她了。", "酒坛里浮起一枚琥珀色铭刻之羽，带着苦酒香。像一个少年醉了一生，只为替她找一个不是背弃的理由。", "旧酒楼"),
    "bailu": _boss("bailu", "白露眠姬", "一夜未眠的少女", "白露清晨，草叶凝满细珠。披发少女坐在露水深处，眼下微青，像整夜都没有睡去。", "她等采药少年带秋草归来，却只等到被露水打湿的药篓。自此再不敢睡，怕一闭眼那人就真的不会回来。", ("露水沾上你的衣角，冷得像一夜未干的泪。", "她努力睁着眼，仿佛睡去就是一种背叛。", "药香清苦，像没来得及煎好的药。"), "她终于慢慢合上眼，草叶上的露珠滚落一地。", "露水中浮起一枚清白铭刻之羽，羽面微凉。像一个少女强撑整夜后，终于落下的第一滴泪。", "露草坡"),
    "qiufen": _boss("qiufen", "分镜书生", "持半面铜镜的少年", "秋分日，窗前铜镜裂成两片。书生捧着其中半面，站在明暗交界处。", "他与闺中少女隔墙借镜光写字，约秋分夜相见。少女被迫远嫁，只送出半片铜镜。", ("断镜映出你的脸，却总缺另一半。", "书生低头擦镜，动作轻得像怕惊动墙那边的人。", "墙上浮出旧日字影，刚成一句，便被秋风擦去。"), "他把半镜贴在墙上，什么也没有照见。", "镜片旁落下一枚银灰铭刻之羽，羽面有细细裂纹。像一段从未见面的相思，偏偏认真了一生。", "旧墙"),
    "hanlu": _boss("hanlu", "采露小僧", "为亡妻采露的少年僧", "寒露夜深，寺外秋草凝霜。年轻僧人提着小瓷瓶弯腰采露，眉眼并不平静。", "未婚妻久病，他听说寒露清晨第一滴露能缓咳血。归来时她已下葬，他剃发后仍夜夜采露。", ("瓷瓶轻响，里面盛着比月光还冷的露。", "他念经时总念错一个字，像旧名卡在喉间。", "秋草被他踏弯，又慢慢立起，仿佛替谁低头。"), "他将瓷瓶放在地上，瓶中寒露慢慢渗入泥土。", "草叶间浮起一枚冷白铭刻之羽，沾着寒露。像少年僧人一生没敢再喊出口的俗名。", "寒寺"),
    "shuangjiang": _boss("shuangjiang", "霜甲归郎", "披霜归来的少年兵", "霜降清晨，城门外马蹄声忽然响起。少年兵披着满身白霜归来。", "他从军时还未及冠，约青梅霜降前归来成亲。战场太远，归书丢在风雪里，红线结也没能送回。", ("霜花爬满他的甲叶，每一片都像未寄出的归书。", "他握着红线结，手指冻得发青，却不肯松开。", "城门很近，可他每走一步都像隔着一场战死的雪。"), "他把红线结放在霜地上，向城中行了一个很轻的军礼。", "霜地上留下一枚银白铭刻之羽，羽根缠着一线淡红。像一个少年用尽一生赶回来的婚约。", "霜城门"),
    "lidong": _boss("lidong", "封书雪女", "把信封进冰河的姑娘", "立冬水寒，河面结起第一层薄冰。白衣姑娘蹲在岸边，把一封信轻轻按进冰下。", "她写好回信，却不敢托人送出，只藏进河边石洞。冬水上涨，信卷入冰下，琴师等不到回信便远走。", ("冰面下有墨迹游动，像鱼，也像未送出的字。", "她的手指冻得通红，却仍把信压得很平。", "远处似有琴声一闪而过，随即被寒水吞没。"), "她看着信纸沉入水底，终于知道琴声不会回来了。", "冰缝里浮起一枚雪白铭刻之羽，羽面有淡淡墨痕。像一封写好了，却亲手封死的信。", "冰河"),
    "xiaoxue": _boss("xiaoxue", "初雪白头女", "第一场雪里等人的少女", "小雪初落，屋檐下站着一位少女。雪花落在她发间，转瞬成霜。", "她与少年约好第一场雪时在老梅树下相见。少年赶赴约定时被雪埋在半路，她等了一夜，满头皆白。", ("雪落无声，却像每一片都在替谁迟到。", "她低头摸着荷包，针脚细密，颜色仍新。", "梅枝未开，树下却已有一地白发似的雪。"), "她把荷包埋在梅树根下，身影随雪淡去。", "梅树下落下一枚雪色铭刻之羽，冰凉而柔软。像少女一夜白头后，仍舍不得丢掉的荷包。", "老梅树"),
    "daxue": _boss("daxue", "埋灯少年", "把灯埋进雪里的少年", "大雪封路，荒村外却有一点灯火从雪下透出。少年跪在雪地里，把灯埋得更深。", "他为晚归的姑娘守夜点灯。风雪太大，他用身体护灯，最后倒在雪里，可灯埋得太深，她没有看见。", ("雪下的灯光一明一暗，像快要断掉的呼吸。", "少年指尖全是冻伤，却仍固执地护着灯盏。", "风雪里似有人喊“小灯郎”，转眼又听不真切。"), "他把熄灭的灯抱进怀里，像终于不再怕风雪。", "雪坑中浮起一枚微暗的铭刻之羽，带着熄灯后的焦香。像一盏没能照到归人的小灯。", "荒村雪路"),
    "dongzhi": _boss("dongzhi", "藏阳小道姑", "守着一簇火的少女", "冬至夜长，破观中亮起一簇小火。小道姑跪坐火前，用袖子护着火苗。", "她曾收留冻伤少年，将暖阳符给他去北地寻亲。少年没能走出风雪，她却日日守火，怕他回来时屋里太冷。", ("火光很小，却把她的影子照得很长。", "她袖口被烧出黑边，仍不肯离火远一点。", "夜风穿过破观，铃铛响得像远行人的脚步。"), "她把最后一根柴放进火里，像终于承认那人不会再冷了。", "火盆旁落下一枚暖金色铭刻之羽，握在手里有微弱余温。像长夜尽处，一碗再也送不出的热粥。", "破观"),
    "xiaohan": _boss("xiaohan", "寒枝狐女", "衔枝筑巢的小狐仙", "小寒风紧，枯树上多出一座歪斜小巢。狐耳少女衔着寒枝站在树下。", "山脚小狐被少年救回旧屋，少年说伤好后可在屋后树上筑巢。后来旧屋空了，她仍年年衔枝回来。", ("她衔着枯枝，说话含混，却固执得可怜。", "旧屋窗洞黑着，像一双再也不会醒来的眼。", "寒风吹乱她的狐尾，她却只顾把小巢补得更紧。"), "她把最后一根枝条放进巢里，蜷成一团随风散去。", "枯巢里落下一枚浅棕铭刻之羽，带着木枝和雪气。像小狐仙年年筑好的巢，仍等着一句“我回来了”。", "旧屋"),
    "dahan": _boss("dahan", "收骨少年", "年尽时收拾旧骨的少年", "大寒岁尽，乱葬坡上风声低沉。少年背着竹篓走来，篓中只有无人认领的旧骨。", "他在义庄帮工，年年去乱葬坡收无名骨，只为找出青梅留下的半支发簪。他怕找不到，也怕真的找到。", ("竹篓轻响，里面的旧骨像在低声道谢。", "少年冻得嘴唇发白，却把每一枚骨都擦得很干净。", "他握着半支发簪，眼神里满是恐惧和希望。"), "他看着竹篓空出的一格，低声说：还没找到，也好。", "寒土上浮起一枚灰白铭刻之羽，羽根像细骨般清瘦。像少年一直不敢确认的那场重逢。", "乱葬坡"),
    "chuxi": _boss("chuxi", "守岁红烛郎", "守着年夜红烛的少年", "除夕夜，旧宅红烛一支支亮起。少年坐在门槛上守岁，身旁空着一只未归人的碗。", "他与远嫁少女约好年夜归家看灯，却等到鸡鸣也无人敲门。后来每年除夕，他都替她守着那盏不会熄的红烛。", ("爆竹声远得像隔着一生。", "红烛滴泪，烛花像小小的伤口。", "他把空碗摆正，像怕归人回来时看见冷清。"), "红烛终于燃尽，少年把空碗收回怀里。", "红烛旁留下一枚暖红铭刻之羽，像年夜里无人听见的一声“回来吃饭”。", "旧宅"),
    "chunjie": _boss("chunjie", "新桃门前女", "贴新桃符的少女", "正月初一，新桃符贴满旧门。少女踮脚抚平红纸，却迟迟不肯关门。", "她年年换新桃，只为等少年在新岁敲门。门漆换了数次，旧人却再也没来拜年。", ("红纸新得刺眼，门里却没有笑声。", "她把桃符贴歪又揭下，像怕错过敲门声。", "新雪落在门槛外，被她扫了一遍又一遍。"), "她轻轻合上门，桃符在风里响了一声。", "门前落下一枚桃红铭刻之羽，带着新纸和旧雪的味道。像一个新年里没等来的敲门声。", "旧门"),
    "yuanxiao": _boss("yuanxiao", "灯市寻郎女", "灯会里找人的姑娘", "元宵灯市人声如潮。少女提着半盏莲灯，在千万灯火中寻找一个约好的人。", "她与少年约在灯谜下相见，却被人潮冲散。后来满城灯火年年亮起，她再也没找到那张熟悉的脸。", ("花灯照得她眼睛很亮，亮得像快要哭出来。", "她每看见一人回头，都会往前走半步。", "灯谜纸条飘落，上面只剩一个未解的“归”字。"), "莲灯熄灭，她停在人潮尽头，不再回头。", "莲灯里飞出一枚明黄铭刻之羽。像满城灯火都亮了，却照不见的那个人。", "灯市"),
    "longtaitou": _boss("longtaitou", "梳龙辫少年", "河边替她梳发的少年", "二月二，河边柳影摇晃。少年握着木梳，身后水纹像一条刚抬头的小龙。", "他学会给姑娘梳龙辫，等她坐到河边。可姑娘被家人送走，他只把那把木梳梳旧了。", ("木梳齿间缠着一缕旧发。", "水里的龙影抬头又沉下，像欲言又止。", "少年把梳子洗了又洗，仍洗不掉等待。"), "他把木梳放进河里，任水纹带走。", "河面浮起一枚青黑铭刻之羽，像一把终于松手的木梳。", "河岸"),
    "huazhao": _boss("huazhao", "百花迟归娘", "花下等春信的少女", "花朝日，百花齐放，花下少女却拆不开一封空白春信。", "她等远行少年寄来花期，却只等到满园花谢。她总疑心信被花香藏住，年年花开都要找一遍。", ("花开得太热闹，越显得她身边寂静。", "她将每朵花都翻过，像找一枚印章。", "空白信纸沾满花粉，仍没有字。"), "她把空信埋在花根下，花香终于淡了。", "花瓣间落下一枚粉白铭刻之羽，像一封被春天弄丢的信。", "花园"),
    "shangsi": _boss("shangsi", "曲水遗簪女", "曲水边失簪的少女", "上巳曲水流觞，水面漂来一支玉簪。少女沿溪追来，裙角尽湿。", "她把心意藏在玉簪里，想借曲水送到少年面前。水流太急，簪子漂远，少年也离席远行。", ("溪水绕过你的脚踝，凉得像迟疑。", "她追着玉簪，不敢喊那人的名字。", "酒杯从水上漂过，无人停下。"), "她终于停步，看着玉簪沉入溪底。", "溪水中浮起一枚玉色铭刻之羽，像一支没送到手里的簪。", "曲水溪"),
    "duanwu": _boss("duanwu", "艾舟沉江郎", "系艾草香囊的少年", "端午江水翻绿，一只系着艾草的小舟从雾中漂来。少年站在船头，香囊早已湿透。", "他为心上人缝了艾草香囊，想赛舟归来送她。江潮翻船，香囊沉底，姑娘此后年年悬艾等舟。", ("艾草香苦，苦得像一口没咽下的告别。", "江面鼓声很远，像从水底传来。", "他摸着湿透的香囊，仍怕她嫌针脚歪。"), "小舟慢慢沉入江雾，艾草香留在水面。", "江雾里落下一枚青绿铭刻之羽，像一枚湿透却仍有香气的香囊。", "江湾"),
    "qixi": _boss("qixi", "星桥错约女", "银河桥下的织梦少女", "七夕夜，星河低垂。少女坐在断开的星桥边，手中织着一段永远接不上的梦。", "她与少年约在鹊桥下看星，却因一场误会错过。桥年年会成，她却总在错的桥头等。", ("星光落在她发间，像细小的针。", "她织出的梦一靠近桥心便断开。", "鹊羽纷纷落下，却没有一只肯带路。"), "她把未织完的梦放进星河，桥光随之暗下。", "星桥边落下一枚银蓝铭刻之羽，像一场只差一步的相逢。", "星桥"),
    "zhongyuan": _boss("zhongyuan", "河灯无归郎", "放河灯的少年魂", "中元夜，河面浮满灯盏。少年蹲在岸边，一盏盏放灯，却没有一盏照回家的路。", "他死在离乡的水路上，忘了归处，只记得有人在家门口等灯。每年中元，他放灯寻路，也替自己寻名。", ("河灯一盏盏远去，像无人认领的名字。", "他把灯推得很轻，怕惊醒水下亡人。", "灯影映出一张年轻却陌生的脸。"), "最后一盏河灯漂远，他站在岸边，像终于不再追。", "河灯旁浮起一枚暖黄铭刻之羽，像一条照不到尽头的归路。", "河岸"),
    "zhongqiu": _boss("zhongqiu", "月下缺环女", "月下戴残玉环的少女", "中秋月圆，少女坐在桂影下，腕上玉环缺了一角。月越圆，她越显得孤单。", "她与少年各执半只玉环，说月圆时合环成亲。少年死在归途，月年年圆，玉环却再也不圆。", ("桂香很淡，淡得像一句被忍住的哭声。", "她抬腕照月，缺口正好盛住一片月光。", "团圆宴的声音从远处传来，与她毫不相干。"), "她把残环放在桂树下，月光慢慢替它补了一瞬。", "桂影中落下一枚月白铭刻之羽，像一只永远缺了一角的玉环。", "桂树下"),
    "chongyang": _boss("chongyang", "茱萸望山郎", "登高望归的少年", "重阳风高，山顶插满茱萸。少年站在最高处，望着一条没有人来的山路。", "他与姑娘约好重阳登高避灾。姑娘被病困在山下，后来再没醒来，他却年年登高，替两人插满茱萸。", ("茱萸香辛，辛得人眼眶发热。", "他把多出的一枝插在身旁空处。", "山路蜿蜒向下，却没有脚步声。"), "他拔下身旁那枝茱萸，轻轻放进风里。", "山风中落下一枚赤褐铭刻之羽，像一枝始终留给她的茱萸。", "高山"),
    "hanyi": _boss("hanyi", "寄衣雪娘", "缝寒衣的姑娘", "寒衣节，针线篮自己翻开。姑娘低头缝着一件寒衣，袖口却永远差最后一针。", "她给远戍少年寄寒衣，衣到边关时人已埋在雪里。她不知死讯，只怪自己缝得太慢。", ("针尖刺破指腹，血珠像一点红线。", "寒衣很厚，却暖不到该暖的人。", "她反复量袖长，像怕那人长高了。"), "她把寒衣叠好，放进没有地址的包袱里。", "针线间浮起一枚灰蓝铭刻之羽，像一件终于缝完却寄不到的寒衣。", "针线房"),
    "xiayuan": _boss("xiayuan", "水官沉愿女", "把愿书沉进水里的少女", "下元夜，水面无风自漩。少女跪在岸边，将愿书一页页沉入水中。", "她求水官赦免心上人的罪，却不知那罪本就是替她顶下。愿书沉了很多年，她才知道错从来不在她。", ("水面吞下愿书，没有一点回声。", "她的袖口全湿，却仍写下一页又一页。", "水下似有人把愿书轻轻推回。"), "她停笔良久，把最后一页愿书撕成两半。", "水中浮起一枚墨蓝铭刻之羽，像一封再也不用替谁求赦的愿书。", "水岸"),
    "laba": _boss("laba", "粥香等归郎", "熬腊八粥的少年", "腊八清晨，旧灶上粥香滚起。少年守着一锅甜粥，不断往空碗里添热气。", "他为赶路姑娘熬腊八粥，想等她进门暖身。姑娘冻死在归路，他却怕粥凉，添柴添到天明。", ("粥香很甜，甜得让人心里发酸。", "他用勺背敲碗，像在催人回家。", "灶火映着他的脸，仍是很年轻的模样。"), "他把那碗粥端到门外，热气散进冷风里。", "灶边落下一枚米白铭刻之羽，像一碗再也没人喝到的腊八粥。", "旧灶"),
    "xiaonian": _boss("xiaonian", "灶前糖衣女", "灶前熬糖的少女", "小年夜，灶前糖香浓得化不开。少女搅着糖浆，案上却压着一纸退婚书。", "她想熬甜糖，请灶王替心上人说几句好话。糖还没冷，退婚书先到，她便年年把苦事熬成甜味。", ("糖丝拉得很长，像断不开的旧缘。", "她笑着尝糖，却被甜得红了眼。", "灶火噼啪，像有人撕碎红纸。"), "她把糖衣浇在退婚书上，纸字慢慢化开。", "糖香里浮起一枚蜜色铭刻之羽，像一句被熬到发苦的好话。", "灶前"),
}


DAILY_BOSS_DEFS: dict[str, BossDef] = {
    "daily_01": _boss(
        "daily_01",
        "雨巷纸伞女",
        "把旧伞撑到天明的少女",
        "细雨落满青石巷，少女撑着一把破纸伞，伞沿滴下的水像没说出口的话。",
        "她曾在雨夜等少年归来，伞下留了一半空位。后来巷口灯灭了很多年，她仍不肯收伞。",
        ("伞骨轻响，像有人迟迟不敢敲门。", "她把伞往旁边偏了偏，仿佛身侧还站着一个人。", "雨水淌过青砖，洗不掉那一夜的等候。"),
        "她终于合上纸伞，雨声也随之轻了下去。",
        "纸伞下落下一枚潮湿的铭刻之羽，像一场等到天明也没有结果的雨。",
        "青石雨巷",
    ),
    "daily_02": _boss(
        "daily_02",
        "荒亭听雪郎",
        "在空亭听雪的少年",
        "荒亭四面漏风，少年独坐石阶，听雪落在旧棋盘上。",
        "他与同窗约好雪夜再下一局。那人赴京后再无音讯，棋盘上的白子便一颗也没落下。",
        ("雪声很轻，却把亭子压得很低。", "他指尖捻着黑子，迟迟不肯落下。", "棋盘另一侧空着，像一个不会再回来的人。"),
        "少年把黑子放回棋盒，亭中雪忽然停了。",
        "棋盘边浮起一枚雪白铭刻之羽，像一局永远轮不到下一手的棋。",
        "荒亭",
    ),
    "daily_03": _boss(
        "daily_03",
        "渡船无灯客",
        "替亡人摆渡的年轻船客",
        "雾水漫过渡口，年轻船客撑着无灯小舟，反复问你要去哪里。",
        "他曾在大雾中错过最后一班归船，自此年年摆渡，只想把别人送到岸上。",
        ("船桨划开水面，没有半点灯影。", "他笑得温和，却总是不敢回头看岸。", "雾中有旧铃声，一响便远了。"),
        "小舟顺雾漂远，船客没有再问归处。",
        "船头留下一枚灰青铭刻之羽，像一盏始终没被点亮的渡灯。",
        "无灯渡口",
    ),
    "daily_04": _boss(
        "daily_04",
        "断琴梨花姬",
        "守着断弦古琴的少女",
        "梨花落满庭院，少女抱着断弦古琴，指尖按在一段再也弹不响的曲上。",
        "她曾为远行少年学一首归来曲。琴练成那日，送回来的却只有一截断弦。",
        ("梨花像雪，却没有雪那样干净。", "琴腹里藏着旧信，字迹早被泪水洇开。", "她每拨一下断弦，庭中便多一分寂静。"),
        "她松开琴弦，满庭梨花一瞬归土。",
        "琴身旁落下一枚梨白铭刻之羽，像一首再也无人听完的曲。",
        "梨花旧庭",
    ),
    "daily_05": _boss(
        "daily_05",
        "红绳井畔郎",
        "把红绳系进井水的少年",
        "古井边垂着一截红绳，少年俯身望井，像想从水里捞回一句承诺。",
        "他与姑娘把红绳系在井畔，说等绳结不褪便成亲。后来绳还红着，人却散了。",
        ("井水倒映着他的脸，年轻得让人难过。", "红绳在风里晃，像谁迟迟不肯松手。", "井底偶尔传来水声，像很远的回信。"),
        "少年把红绳剪断，半截沉进井底。",
        "井沿浮起一枚暗红铭刻之羽，像一段终于放手的旧约。",
        "古井",
    ),
    "daily_06": _boss(
        "daily_06",
        "药炉守火童",
        "守着一炉冷药的少年",
        "药庐炉火半明半灭，少年抱膝坐在炉前，药香苦得像一场旧病。",
        "他替师姐守药三日，药成时人却已经远葬山后。自此炉火不熄，药再也无人喝。",
        ("药烟盘在梁上，迟迟不肯散。", "少年掌心满是烫痕，却仍护着火口。", "窗外草木青了又枯，药味一年比一年淡。"),
        "少年掀开药盖，把冷药倒进土里。",
        "炉灰里浮起一枚苦香铭刻之羽，像一碗迟来的药。",
        "药庐",
    ),
    "daily_07": _boss(
        "daily_07",
        "落梅书信女",
        "在梅树下拆信的少女",
        "梅枝斜出墙头，少女站在树下，一封旧信拆到一半便再也拆不动。",
        "信中本该是少年报平安的话，可她怕看见别离，也怕看不见归期。",
        ("梅香很冷，冷得像纸上没干的墨。", "她把信角抚平，又轻轻折回去。", "风一吹，梅瓣落得像没寄出的字。"),
        "她把信放回袖中，转身时梅花尽落。",
        "梅枝间落下一枚淡粉铭刻之羽，像一封终究没敢读完的信。",
        "梅墙",
    ),
    "daily_08": _boss(
        "daily_08",
        "旧桥挑灯客",
        "在断桥上挑灯的人",
        "断桥只剩半截，挑灯客站在桥心，灯火照不到对岸。",
        "他曾答应送妹妹过桥去看灯会。洪水断桥那夜，他把灯举了一生，也没照见她回来。",
        ("灯火一晃，桥下水声便重一分。", "他的肩膀很瘦，灯杆却握得极稳。", "对岸空空，像所有热闹都与他无关。"),
        "挑灯客把灯挂在桥头，自己随水雾散去。",
        "灯穗上落下一枚昏黄铭刻之羽，像一盏照不到亲人的灯。",
        "断桥",
    ),
    "daily_09": _boss(
        "daily_09",
        "寒窗墨影郎",
        "在旧窗前磨墨的书生",
        "寒窗外霜花凝结，书生低头磨墨，砚中墨色深得像一眼不见底的井。",
        "他想写完功名信再去提亲，却把一生都写在了赶考路上。姑娘嫁衣旧了，信也没寄出。",
        ("墨香很沉，压得灯芯直不起身。", "他写下她的名字，又用袖子轻轻擦掉。", "窗纸上有雪光，像多年后的白发。"),
        "书生合上砚台，满屋墨色慢慢散开。",
        "砚边留下一枚墨黑铭刻之羽，像一封迟到太久的功名信。",
        "寒窗书屋",
    ),
    "daily_10": _boss(
        "daily_10",
        "空楼绣鞋娘",
        "守着一双绣鞋的姑娘",
        "空楼上红线绕梁，姑娘捧着一双未穿过的绣鞋，鞋面鸳鸯只绣成一半。",
        "她给自己绣嫁鞋，等少年带她离开高楼。门锁换了一重又一重，鞋底却从未沾尘。",
        ("红线垂下，像无数条不能走的路。", "她把鞋尖贴近耳边，像听见旧日脚步。", "楼外市声很远，远得像另一个人间。"),
        "姑娘把绣鞋放在窗边，红线一寸寸断开。",
        "鞋面上浮起一枚绯红铭刻之羽，像一双再也没能出门的嫁鞋。",
        "空楼",
    ),
    "daily_11": _boss(
        "daily_11",
        "白鹤归山女",
        "等白鹤传书的少女",
        "山云低垂，少女站在崖边，掌心停着一根折断的白鹤羽。",
        "少年说会托白鹤送信，她便每日来山口等。鹤群飞过了许多年，没有一只落下。",
        ("崖风吹得衣袖鼓起，像一封空信。", "她抬头数鹤，数到后来忘了自己的年岁。", "云影落在她脚边，又匆匆离开。"),
        "少女把断羽放进风里，白鹤声渐渐远去。",
        "崖边落下一枚素白铭刻之羽，像一封终于承认不会抵达的信。",
        "归鹤崖",
    ),
    "daily_12": _boss(
        "daily_12",
        "残荷采莲郎",
        "在残荷间采莲的少年",
        "池中残荷倾斜，少年赤脚下水，怀里抱着一捧早已枯黑的莲蓬。",
        "他答应给病中的姑娘带第一颗莲子。等他回来，窗前药碗已经凉透。",
        ("荷叶擦过水面，像很轻的叹息。", "少年把莲子一颗颗数过，生怕少了。", "池水没过膝盖，凉得像旧日病榻。"),
        "少年把莲子撒进池中，残荷一瞬低头。",
        "枯莲旁浮起一枚青褐铭刻之羽，像一颗再也送不到手里的莲子。",
        "残荷池",
    ),
    "daily_13": _boss(
        "daily_13",
        "孤灯裁衣女",
        "在灯下裁旧衣的少女",
        "孤灯摇曳，少女拿着剪刀，把一件旧衣裁了又缝。",
        "她想把少年留下的旧衣改成孩子衣裳，可少年没归，孩子也从未到来。",
        ("剪刀开合声很轻，却像割开许多年。", "旧衣上还有尘土和远路味。", "她把袖口量了又量，始终舍不得剪下最后一寸。"),
        "少女吹灭孤灯，旧衣伏在案上像终于睡去。",
        "灯芯旁落下一枚灰蓝铭刻之羽，像一件没有等来主人的旧衣。",
        "裁衣房",
    ),
    "daily_14": _boss(
        "daily_14",
        "沙洲望潮客",
        "在退潮后等船的人",
        "沙洲露出水面，望潮客站在贝壳碎影里，看一条早已不来的航线。",
        "他送爱人出海采珠，说潮起时接她回家。那日以后，海潮年年来，船却再也没有。",
        ("海风带着咸味，像被忍住的泪。", "他把贝壳排成航线，又被潮水冲乱。", "远处白浪翻起，总像一艘船的影子。"),
        "望潮客蹲下身，把最后一枚贝壳埋进沙里。",
        "沙中浮起一枚浅蓝铭刻之羽，像一片没有带回人的潮水。",
        "望潮沙洲",
    ),
    "daily_15": _boss(
        "daily_15",
        "铜铃守门童",
        "替旧宅守门的少年",
        "旧宅铜铃无风自响，少年坐在门槛上，怀中抱着一把锈钥匙。",
        "主人一家避祸远走，说三日便归。他守到钥匙生锈，也没敢把门锁上。",
        ("铜铃声空空，像从空宅深处传来。", "少年把钥匙擦亮一点，又很快被泪水打湿。", "门内落满灰尘，却仍留着归人的位置。"),
        "少年把钥匙放到门缝里，铜铃声终于停了。",
        "门槛上落下一枚铜色铭刻之羽，像一把守到最后也没用上的钥匙。",
        "旧宅门前",
    ),
    "daily_16": _boss(
        "daily_16",
        "霜林拾叶女",
        "在霜林里拾叶的少女",
        "霜林叶落成堆，少女把每片红叶翻过，像在找一行早已褪色的字。",
        "少年曾把告别写在红叶背面，托风送来。她只收到满山红叶，却不知哪一片是他。",
        ("霜很薄，薄得像纸上的遗憾。", "她指尖冻红，仍把叶脉看得仔细。", "风吹过林梢，带走一片又一片来不及读的叶。"),
        "少女把红叶抱进怀里，整片霜林慢慢空了。",
        "叶堆中浮起一枚赤金铭刻之羽，像一片终于被找到的告别。",
        "霜林",
    ),
    "daily_17": _boss(
        "daily_17",
        "夜市卖花郎",
        "在收摊后仍卖花的少年",
        "夜市灯火将熄，少年守着一篮蔫花，仍问路人要不要买一枝。",
        "他想攒钱给姑娘买药，却在药铺关门前倒下。花卖不完，病也没等人。",
        ("花香淡得快要消失。", "他把最好的一枝藏在篮底，说那枝不卖。", "街上人声散尽，只剩零钱撞在碗里。"),
        "少年把最后一枝花放在空药铺门前。",
        "花篮里落下一枚淡紫铭刻之羽，像一枝没能换成药的花。",
        "夜市",
    ),
    "daily_18": _boss(
        "daily_18",
        "月井捞影女",
        "在井中捞月影的少女",
        "月光落进古井，少女俯身用银勺捞影，勺中永远只有一片碎光。",
        "她听说井月能照见亡人，便夜夜来捞。月亮圆了又缺，她只捞起自己的影子。",
        ("银勺碰到井壁，响声清冷。", "她不敢眨眼，怕错过水面一瞬的旧脸。", "井口月光晃动，像有人轻轻转身。"),
        "少女把银勺放进井里，月影碎成千万点。",
        "井水上浮起一枚月银铭刻之羽，像一轮永远捞不上来的月。",
        "月井",
    ),
    "daily_19": _boss(
        "daily_19",
        "竹窗等笛郎",
        "在竹窗下等笛声的少年",
        "竹影敲窗，少年倚着窗棂，手里握着一支没有吹响的短笛。",
        "邻家姑娘说听见笛声便会翻墙来看他。后来墙拆了，人远嫁了，笛声也哑了。",
        ("竹叶拍窗，像很轻的暗号。", "少年把笛孔擦了一遍又一遍。", "月色从窗格漏下，落在他空着的身侧。"),
        "少年把短笛挂在竹枝上，竹影随风远去。",
        "短笛旁落下一枚青绿铭刻之羽，像一段再也没人回应的暗号。",
        "竹窗",
    ),
    "daily_20": _boss(
        "daily_20",
        "旧驿牵马客",
        "在荒驿牵空马的人",
        "荒驿门前拴着一匹空鞍马，牵马客拍着马颈，像安慰一个迟迟不归的旅人。",
        "他替好友看马，说等人取回便饮一坛酒。后来马老了，酒封泥也碎了。",
        ("马铃轻响，驿道上却没有尘。", "他把鞍子擦得发亮，像随时能出发。", "酒坛埋在墙根，香气早已散尽。"),
        "牵马客解开缰绳，空鞍马慢慢走入暮色。",
        "马鞍上落下一枚棕金铭刻之羽，像一场没有等到归人的饯别。",
        "荒驿",
    ),
    "daily_21": _boss(
        "daily_21",
        "灯花照影女",
        "数灯花到天明的少女",
        "屋内油灯结花，少女伏在桌边，一朵一朵数着将落未落的灯花。",
        "她听说灯花落几朵，远人便归几日。后来灯花落尽，远人也没有走近一步。",
        ("灯油快干，火光却倔强地亮着。", "她数错了便重来，像只要认真就能改命。", "窗外天色泛白，屋里仍像昨夜。"),
        "少女轻轻吹灯，最后一朵灯花落在掌心。",
        "灯盏中浮起一枚暖黄铭刻之羽，像一夜数到尽头的盼望。",
        "灯下小屋",
    ),
    "daily_22": _boss(
        "daily_22",
        "雪庙还愿郎",
        "在雪庙前还愿的少年",
        "破庙香灰被雪压住，少年跪在阶前，把三炷断香扶正。",
        "他曾许愿让姑娘平安活下去，愿成之后，姑娘忘了他。他还是年年来还愿，不敢怨神。",
        ("断香冒出一点青烟，转眼被雪吞没。", "少年额头贴着冰冷石阶，声音很轻。", "庙门半掩，神像也像不忍看他。"),
        "少年把断香插进雪中，叩首后化作清烟。",
        "香灰里落下一枚灰白铭刻之羽，像一个愿成之后无人记得的名字。",
        "雪庙",
    ),
    "daily_23": _boss(
        "daily_23",
        "柳岸放舟女",
        "在柳岸放纸舟的姑娘",
        "柳岸水浅，姑娘蹲在岸边，一只只放走写满名字的纸舟。",
        "她把没敢说的话折成纸舟，盼它漂到少年所在的城。可水太浅，舟总困在岸边。",
        ("柳絮落在水上，像无数细小白字。", "她推舟时很轻，怕把名字弄皱。", "纸舟碰到岸石，便慢慢洇开。"),
        "姑娘放开最后一只纸舟，柳影把它送得很远。",
        "水边浮起一枚浅青铭刻之羽，像一只终于离岸的纸舟。",
        "柳岸",
    ),
    "daily_24": _boss(
        "daily_24",
        "残月守碑客",
        "在残月下擦碑的人",
        "残月挂在荒陵上，守碑客用袖口擦拭无名石碑，擦到指节发白。",
        "他答应替战死的朋友刻名，却在乱世里弄丢了名册。此后每座无名碑，他都当作那个人。",
        ("碑面冰冷，映不出完整月色。", "他一遍遍擦去青苔，像能擦回一个名字。", "荒草起伏，似有很多人低声答应。"),
        "守碑客停下手，向所有无名碑深深一礼。",
        "碑影中落下一枚银灰铭刻之羽，像一个终于被记住的无名人。",
        "荒陵",
    ),
}


ALL_BOSS_DEFS: dict[str, BossDef] = {**BOSS_DEFS, **DAILY_BOSS_DEFS}


class SeasonalBossService(CoreService):
    """按节令出现的岁时情劫首领。"""

    def status(self, client_id: str) -> str:
        """查看今日岁时情劫。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        event = self._today_event(create=True, update=True)
        if not event:
            pending = self._latest_rewardable(client_id)
            next_info = self._next_boss_text()
            if pending:
                return T.hint("今日无岁时情劫，但你有首领奖励待领取。", "发送：首领奖励<首领奖励>")
            return T.hint("今日无岁时情劫。", next_info)
        return self._format_status(event)

    def ranking(self, client_id: str) -> str:
        """查看今日或最近首领排行。"""

        _, error = self.require_player(client_id)
        if error:
            return error
        event = self._today_event(create=False, update=True) or self._latest_event()
        if not event:
            return T.hint("暂无岁时情劫记录。", self._next_boss_text())
        rows = self._participants(event["event_id"])
        if not rows:
            return T.hint(f"{event['boss_name']} 暂无挑战记录。", "发送：挑战首领 参与今日岁时情劫。<挑战首领>")
        panel = T.panel()
        panel.section(f"岁时情劫排行·{event['boss_name']}")
        for index, row in enumerate(rows[:10], start=1):
            panel.line(
                f"{index}. {self.format_player_name(row['client_id'])} "
                f"伤害 **{row['damage']}**｜贡献 {self._contribution(row['damage'], event):.1%}｜"
                f"挑战{row['challenge_count']}/{SEASONAL_BOSS_MAX_CHALLENGES}次"
            )
        return panel.render()

    def challenge(self, client_id: str) -> str | dict:
        """挑战今日岁时情劫。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        self.cleanup_battle_records()
        assert player is not None

        event = self._today_event(create=True, update=True)
        if not event:
            return T.hint("今日无岁时情劫。", self._next_boss_text())
        if event["status"] != "开启":
            return T.hint(f"{event['boss_name']} 已经{event['status']}，不能继续挑战。", "发送：首领奖励 查看是否可以领取奖励。<首领奖励>")
        if player["status"] != "空闲":
            return self._busy_challenge_hint(player["status"])
        if int(player["hp"]) <= 0:
            return T.hint("血气不足，无法挑战首领。", "发送：休息，时间到后发送：结束休息")

        check = self._challenge_check(event["event_id"], client_id)
        if check:
            return check

        result = self._fight_boss(player, event)
        damage = min(int(result["damage"]), int(event["hp"]))
        killed = False
        challenge_record_id = 0
        with self.db.transaction() as conn:
            fresh = conn.execute(
                "SELECT * FROM seasonal_boss_events WHERE event_id = ? AND status = '开启'",
                (event["event_id"],),
            ).fetchone()
            if not fresh:
                return T.hint("今日岁时情劫已经结束。", "发送：首领奖励 查看是否可以领取奖励。<首领奖励>")
            fresh_player = conn.execute(
                "SELECT status, hp FROM players WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not fresh_player:
                return T.hint("你还没有创建用户。", "发送：创建用户 名称，例如：创建用户 青衫客")
            if str(fresh_player["status"]) != "空闲":
                return self._busy_challenge_hint(str(fresh_player["status"]))
            if int(fresh_player["hp"]) <= 0:
                return T.hint("血气不足，无法挑战首领。", "发送：休息，时间到后发送：结束休息")
            current = conn.execute(
                """
                SELECT challenge_count, last_challenge_at
                FROM seasonal_boss_participants
                WHERE event_id = ? AND client_id = ?
                """,
                (event["event_id"], client_id),
            ).fetchone()
            if current and int(current["challenge_count"]) >= SEASONAL_BOSS_MAX_CHALLENGES:
                return T.hint("今日挑战次数已用完。", "等下一次岁时情劫出现后再挑战。")
            if current:
                last = dt(current["last_challenge_at"])
                left = timedelta(minutes=SEASONAL_BOSS_CHALLENGE_COOLDOWN_MINUTES) - (now() - last) if last else timedelta()
                if left > timedelta():
                    seconds = max(1, int(left.total_seconds()))
                    return T.hint(f"岁时旧念尚未重新凝形，还需 {seconds // 60}分{seconds % 60}秒。", "稍后再发送：挑战首领<挑战首领>")

            damage = min(damage, int(fresh["hp"]))
            left_hp = max(0, int(fresh["hp"]) - damage)
            killed = left_hp <= 0
            result["event_id"] = int(event["event_id"])
            result["boss_name"] = str(event["boss_name"])
            result["boss_label"] = "首领"
            result["damage"] = damage
            result["hp_before"] = int(fresh["hp"])
            result["hp_after"] = left_hp
            result["killed"] = killed
            result["player_max_hp"] = int(player["max_hp"])
            result["player_max_mp"] = int(player["max_mp"])
            conn.execute(
                "UPDATE players SET hp = ?, mp = ? WHERE client_id = ?",
                (result["hp_left"], result["mp_left"], client_id),
            )
            self.reset_rest_window_conn(conn, client_id, int(result["hp_left"]), int(result["mp_left"]))
            conn.execute(
                """
                INSERT INTO seasonal_boss_participants
                (event_id, client_id, damage, challenge_count, last_challenge_at, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(event_id, client_id)
                DO UPDATE SET
                    damage = damage + excluded.damage,
                    challenge_count = challenge_count + 1,
                    last_challenge_at = excluded.last_challenge_at,
                    updated_at = excluded.updated_at
                """,
                (event["event_id"], client_id, damage, ts(), ts(), ts()),
            )
            cursor = conn.execute(
                """
                INSERT INTO boss_challenge_records
                (event_id, client_id, damage, hp_before, hp_after, killed, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    client_id,
                    damage,
                    int(fresh["hp"]),
                    left_hp,
                    1 if killed else 0,
                    dump_json(result),
                    ts(),
                ),
            )
            challenge_record_id = int(cursor.lastrowid)
            if killed:
                conn.execute(
                    """
                    UPDATE seasonal_boss_events
                    SET hp = 0, status = '已击破', killed_at = ?, result = ?
                    WHERE event_id = ?
                    """,
                    (ts(), dump_json({"killer": client_id}), event["event_id"]),
                )
            else:
                conn.execute(
                    "UPDATE seasonal_boss_events SET hp = ? WHERE event_id = ?",
                    (left_hp, event["event_id"]),
                )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '挑战首领', ?, ?)",
                (
                    client_id,
                    (
                        f"event={event['event_id']}, boss={event['boss_name']}, damage={damage}, "
                        f"weapon_exp={int(result.get('weapon_exp', 0)) if int(result.get('weapon_id', 0)) > 0 else 0}"
                    ),
                    ts(),
                ),
            )
            self.record_weapon_combat_conn(
                conn,
                client_id,
                int(result.get("weapon_id", 0)),
                boss_challenge=True,
                damage=int(result.get("highest_damage", damage)),
                weapon_exp=int(result.get("weapon_exp", 0)),
            )
            if killed:
                zhenyuan_zhuxie_service.grant_boss_points_conn(
                    conn,
                    client_id,
                    1,
                    source_name=str(event["boss_name"]),
                    source_key=str(event["event_id"]),
                    source_module="首领",
                    extra={"location_name": str(event["location_name"]), "weight_type": str(event.get("weight_type") or "")},
                )

        atmosphere = random.choice(load_json(event["atmosphere"], [])) if event["atmosphere"] else event["scene"]
        return self._challenge_log_block(
            title=f"挑战岁时情劫：{event['boss_name']}",
            subtitle=atmosphere,
            boss_name=event["boss_name"],
            player=player,
            result=result,
            damage=damage,
            left_hp=left_hp,
            max_hp=int(event["max_hp"]),
            killed=killed,
            killed_text=f"{event['boss_name']} 已被送回岁时深处，可以领取首领奖励。",
            alive_text=f"再次挑战需等待 {SEASONAL_BOSS_CHALLENGE_COOLDOWN_MINUTES} 分钟。",
            hurt_text="你被旧念重伤，建议先休息。",
            reward_command="首领奖励",
            challenge_command="挑战首领",
            log_kind="boss",
            record_id=challenge_record_id,
        )

    def reward(self, client_id: str) -> str:
        """领取最近一次可领取的首领奖励。"""

        player, error = self.require_player(client_id)
        if error:
            return error
        assert player is not None
        self._today_event(create=False, update=True)
        event = self._latest_rewardable(client_id)
        if not event:
            active = self._today_event(create=False, update=False)
            if active:
                return T.hint("今日岁时情劫还没有结束。", "继续挑战，或等其被击破/次日 04:00 退去后再发送：首领奖励<首领奖励>")
            return T.hint("没有可领取的首领奖励。", "发送：首领 查看今日是否有岁时情劫。<首领>")

        participant = self.db.fetch_one(
            "SELECT * FROM seasonal_boss_participants WHERE event_id = ? AND client_id = ?",
            (event["event_id"], client_id),
        )
        if not participant:
            return T.hint("你没有参与这次岁时情劫。", "下一次出现时发送：挑战首领<挑战首领>")
        if int(participant["reward_claimed"]):
            return participant["reward_text"] or T.hint("奖励已经领取。", "发送：首领 查看今日状态。<首领>")

        reward = self._roll_reward(event, participant, player)
        with self.db.transaction() as conn:
            fresh = conn.execute(
                "SELECT reward_claimed FROM seasonal_boss_participants WHERE event_id = ? AND client_id = ?",
                (event["event_id"], client_id),
            ).fetchone()
            if not fresh or int(fresh["reward_claimed"]):
                return T.hint("奖励已经领取。", "发送：首领 查看今日状态。<首领>")
            old_level, new_level = self.add_exp_conn(conn, client_id, reward["exp"])
            conn.execute(
                "UPDATE players SET source_stones = source_stones + ? WHERE client_id = ?",
                (reward["stones"], client_id),
            )
            for item_id, quantity in reward["ring_items"]:
                self.add_ring_conn(conn, client_id, item_id, quantity)
            for gem_id, level, quantity in reward["gems"]:
                self.add_gem_conn(conn, client_id, gem_id, level, quantity)
            feather_lines = []
            for _ in range(reward["feathers"]):
                cursor = conn.execute(
                    """
                    INSERT INTO inscription_feathers
                    (client_id, source_key, source_name, title, flavor_text, obtained_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client_id,
                        event["boss_key"],
                        event["boss_name"],
                        f"{event['boss_name']}遗羽",
                        event["feather_text"],
                        ts(),
                    ),
                )
                feather_lines.append(f"获得铭刻之羽 〔{int(cursor.lastrowid)}〕：{event['boss_name']}遗羽")
            weapon_texts = []
            for drop in reward.get("weapons", []):
                weapon_id = weapon_service.create_weapon_conn(
                    conn,
                    client_id,
                    drop["weapon_def_id"],
                    drop["quality"],
                    drop["max_level"],
                    equipped=False,
                )
                weapon_texts.append(f"获得武器 {weapon_id_label(weapon_id)} {drop['name']}[{drop['quality']}] 上限{drop['max_level']}")

            # --- 异火掉落判定 ---
            flame_result = flame_service.roll_boss_wormhole_flame(conn, client_id)
            flame_lines = []
            if flame_result.get("granted"):
                flame_lines.append(f"🔥 {flame_result['text']}")
            elif flame_result.get("compensation"):
                comp = flame_result["compensation"]
                flame_lines.append(f"🔥 {flame_service.compensation_text(comp['flame_name'], comp.get('reason', ''))}")

            lines = [
                f"岁时情劫奖励：{event['boss_name']}",
                f"结果：{event['status']}｜贡献：{reward['contribution']:.1%}，排名：{reward['rank']}",
                f"首领权重：{event['weight_type']}｜珍贵抽取：{reward.get('loot_rolls', 1)} 次",
                f"宗门增益：珍贵掉落 +{float(reward.get('influence_bonus') or 0.0):.1%}",
                f"源石+{money(reward['stones'])}，经验+{reward['exp']}",
            ]
            if new_level > old_level:
                lines.append(f"等级提升：{old_level} → {new_level}")
            lines.extend(reward["item_texts"])
            lines.extend(feather_lines)
            if feather_lines:
                lines.append(event["feather_text"])
            lines.extend(weapon_texts)
            lines.extend(flame_lines)
            text = "\n".join(lines)
            conn.execute(
                """
                UPDATE seasonal_boss_participants
                SET reward_claimed = 1, reward_text = ?, updated_at = ?
                WHERE event_id = ? AND client_id = ?
                """,
                (text, ts(), event["event_id"], client_id),
            )
            conn.execute(
                "INSERT INTO game_logs (client_id, action, detail, created_at) VALUES (?, '领取首领奖励', ?, ?)",
                (
                    client_id,
                    (
                        f"event_id={event['event_id']}, boss={event['boss_name']}, "
                        f"rank={reward['rank']}, exp={reward['exp']}, stones={reward['stones']}, "
                        f"feathers={reward['feathers']}"
                    ),
                    ts(),
                ),
            )
            record_sect_merit_conn(
                conn,
                client_id,
                "influence",
                max(25, int(reward["contribution"] * 1200) + (100 if int(reward["rank"]) <= 3 else 0)),
                source="领取首领奖励",
                detail=f"event_id={event['event_id']}, boss={event['boss_name']}, rank={reward['rank']}",
            )
        return text

    def _today_event(self, create: bool, update: bool) -> dict[str, Any] | None:
        """读取或生成当前业务日首领。"""

        day = self._business_date()
        if update:
            self._close_expired_events()
        event = self.db.fetch_one(
            "SELECT * FROM seasonal_boss_events WHERE business_day = ?",
            (day.isoformat(),),
        )
        if event:
            return dict(event)
        if not create:
            return None
        boss_def, event_type, weight_type = self._boss_for_date(day)
        if not boss_def:
            return None
        return self._open_event(day, boss_def, event_type, weight_type)

    def _open_event(self, day: date, boss_def: BossDef, event_type: str, weight_type: str) -> dict[str, Any]:
        """按当前服务器生态生成今日首领。"""

        snapshot = self._world_snapshot()
        level = max(3, min(MAX_LEVEL, snapshot["median_level"] + random.randint(-3, 5)))
        median_attack = max(8, snapshot["median_attack"])
        median_hp = max(120, snapshot["median_hp"])
        defense = max(1, int(median_attack * random.uniform(0.32, 0.52)))
        attack = max(1, int(median_hp / 22 + level * 1.5))
        one_challenge_damage = self._estimate_challenge_damage(median_attack, level, defense)
        expected_players = max(3, min(30, round(snapshot["active_count"] * 0.65)))
        expected_attempts = 4 if weight_type == "普通节气" else SEASONAL_BOSS_MAX_CHALLENGES
        difficulty = random.uniform(1.25, 1.55)
        max_hp = max(360, int(one_challenge_damage * expected_players * expected_attempts * difficulty))
        opened = datetime.combine(day, time(hour=DAY_RESET_HOUR))
        closes = opened + timedelta(days=1)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO seasonal_boss_events (
                    business_day, boss_key, event_type, weight_type, boss_name, title,
                    scene, story, farewell, feather_text, location_name, atmosphere,
                    level, max_hp, hp, attack, defense, difficulty,
                    status, opened_at, closes_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '开启', ?, ?)
                """,
                (
                    day.isoformat(),
                    boss_def.key,
                    event_type,
                    weight_type,
                    boss_def.name,
                    boss_def.title,
                    boss_def.scene,
                    boss_def.story,
                    boss_def.farewell,
                    boss_def.feather_text,
                    boss_def.location,
                    dump_json(list(boss_def.atmosphere)),
                    level,
                    max_hp,
                    max_hp,
                    attack,
                    defense,
                    difficulty,
                    ts(opened),
                    ts(closes),
                ),
            )
            event_id = int(cursor.lastrowid)
        event = self.db.fetch_one("SELECT * FROM seasonal_boss_events WHERE event_id = ?", (event_id,))
        assert event is not None
        return dict(event)

    def _close_expired_events(self) -> None:
        """过了次日 04:00，未击破的首领会退去。"""

        self.db.execute(
            """
            UPDATE seasonal_boss_events
            SET status = '已退去', result = ?
            WHERE status = '开启' AND closes_at <= ?
            """,
            (dump_json({"reason": "timeout"}), ts()),
        )

    def _latest_event(self) -> dict[str, Any] | None:
        """读取最近一条首领记录。"""

        row = self.db.fetch_one("SELECT * FROM seasonal_boss_events ORDER BY opened_at DESC LIMIT 1")
        return dict(row) if row else None

    def _latest_rewardable(self, client_id: str) -> dict[str, Any] | None:
        """读取玩家最近可领取的首领。"""

        row = self.db.fetch_one(
            """
            SELECT e.*
            FROM seasonal_boss_events e
            JOIN seasonal_boss_participants p ON p.event_id = e.event_id
            WHERE p.client_id = ?
              AND p.reward_claimed = 0
              AND e.status IN ('已击破', '已退去')
            ORDER BY e.opened_at DESC
            LIMIT 1
            """,
            (client_id,),
        )
        return dict(row) if row else None

    def _challenge_check(self, event_id: int, client_id: str) -> str:
        """检查挑战次数和 30 分钟冷却。"""

        row = self.db.fetch_one(
            """
            SELECT challenge_count, last_challenge_at
            FROM seasonal_boss_participants
            WHERE event_id = ? AND client_id = ?
            """,
            (event_id, client_id),
        )
        if not row:
            return ""
        if int(row["challenge_count"]) >= SEASONAL_BOSS_MAX_CHALLENGES:
            return T.hint("今日挑战次数已用完。", "等下一次岁时情劫出现后再挑战。")
        last = dt(row["last_challenge_at"])
        if not last:
            return ""
        left = timedelta(minutes=SEASONAL_BOSS_CHALLENGE_COOLDOWN_MINUTES) - (now() - last)
        if left <= timedelta():
            return ""
        seconds = max(1, int(left.total_seconds()))
        return T.hint(f"岁时旧念尚未重新凝形，还需 {seconds // 60}分{seconds % 60}秒。", "稍后再发送：挑战首领")

    @staticmethod
    def _busy_challenge_hint(status: str) -> str:
        """玩家本体忙碌时，解释为什么不能挑战首领。"""

        if status == "探险中":
            return T.hint(
                "本体正在探险，不能挑战首领。",
                "行商化身仍可跑商；先发送：探险状态，30 分钟后发送：结束探险，再发送：挑战首领",
            )
        return T.hint(f"当前状态为 {status}，不能挑战首领。", "先结束当前状态，再发送：挑战首领")

    def _fight_boss(self, player: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        """结算一次挑战；只算数值和逐次出手日志，不写数据库。"""

        action_limit = 10 + min(4, int(player["level"]) // 25)
        return combat_service.fight_boss(
            player,
            event,
            boss_kind=self._boss_kind(event),
            action_limit=action_limit,
        )

    @staticmethod
    def _boss_kind(event: dict[str, Any]) -> str:
        """按首领 key 稳定映射战斗类型，不额外建表。"""

        kinds = ("游魂", "鬼", "妖", "魔", "古卫")
        key = str(event.get("boss_key") or event.get("boss_name") or "")
        return kinds[sum(ord(char) for char in key) % len(kinds)]

    def _challenge_log_block(
        self,
        *,
        title: str,
        subtitle: str,
        boss_name: str,
        player: dict[str, Any],
        result: dict[str, Any],
        damage: int,
        left_hp: int,
        max_hp: int,
        killed: bool,
        killed_text: str,
        alive_text: str,
        hurt_text: str,
        reward_command: str,
        challenge_command: str,
        log_kind: str = "",
        record_id: int = 0,
    ) -> str | dict:
        """按玩家设置返回首领挑战短摘要，并附带战斗日志链接。"""

        return combat_log_text.boss_brief(
            title=title,
            subtitle=subtitle,
            boss_name=boss_name,
            boss_label="首领",
            player=player,
            result=result,
            damage=damage,
            left_hp=left_hp,
            max_hp=max_hp,
            killed=killed,
            killed_text=killed_text,
            alive_text=alive_text,
            hurt_text=hurt_text,
            log_kind=log_kind,
            record_id=record_id,
            client_id=str(player["client_id"]),
            detail=combat_log_text.wants_detail(player),
        )

    def _roll_reward(self, event: dict[str, Any], participant: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
        """按贡献、排名和节日权重生成奖励。"""

        rows = self._participants(event["event_id"])
        rank = next((index for index, row in enumerate(rows, start=1) if row["client_id"] == player["client_id"]), len(rows))
        contribution = self._contribution(int(participant["damage"]), event)
        contribution_score = self._contribution_score(contribution)
        killed_factor = 1.0 if event["status"] == "已击破" else 0.55
        weight = str(event["weight_type"])
        rates = self._reward_rates(weight)
        influence_bonus = self._public_battle_reward_bonus(str(player["client_id"]))
        rank_factor = {1: 1.18, 2: 1.08, 3: 1.0}.get(rank, 0.92)
        stones = max(1, int((int(event["level"]) * 850 + int(event["max_hp"]) * 0.015) * killed_factor * (0.45 + contribution_score * 2.5) * rank_factor))
        exp = max(1, int(monster_exp(event["level"], 1.8, player["level"]) * killed_factor * (0.6 + contribution_score * 1.8)))
        loot_rolls = self._good_loot_rolls(contribution_score, rank, influence_bonus)
        ring_items: list[tuple[str, int]] = []
        gems: list[tuple[str, int, int]] = []
        item_texts: list[str] = []
        feathers = 0
        weapons: list[dict[str, Any]] = []
        location_name = str(event["location_name"])

        recover = self._random_equipment_item("恢复类")
        if recover:
            ring_items.append((recover["ring_item_id"], 1))
            item_texts.append(f"纳戒获得 {recover['name']} x1")
        for _ in range(loot_rolls):
            reward = self._roll_good_loot(
                weight,
                contribution_score,
                rank,
                rates,
                location_name,
                influence_bonus,
                allow_feather=feathers <= 0,
            )
            if not reward:
                continue
            kind = reward["kind"]
            if kind == "feather":
                feathers += 1
            elif kind == "gem":
                gems.append((reward["item_id"], int(reward["level"]), 1))
                item_texts.append(f"宝石获得 {reward['name']} {int(reward['level'])}级 x1")
            elif kind == "weapon":
                weapons.append(reward["drop"])
            else:
                ring_items.append((reward["item_id"], 1))
                item_texts.append(f"纳戒获得 {reward['name']} x1")
        return {
            "rank": rank,
            "contribution": contribution,
            "loot_rolls": loot_rolls,
            "stones": stones,
            "exp": exp,
            "feathers": feathers,
            "ring_items": ring_items,
            "gems": gems,
            "weapons": weapons,
            "weapon": weapons[0] if weapons else None,
            "item_texts": item_texts,
            "influence_bonus": influence_bonus,
        }

    def _reward_rates(self, weight_type: str) -> dict[str, float]:
        """从数据库读取首领掉落概率；缺失时用每日旧愿兜底。"""

        row = self.db.fetch_one(
            "SELECT * FROM seasonal_boss_reward_rates WHERE weight_type = ?",
            (weight_type,),
        ) or self.db.fetch_one(
            "SELECT * FROM seasonal_boss_reward_rates WHERE weight_type = '每日旧愿'",
        )
        if not row:
            return {
                "feather_chance": 0.025,
                "feather_rank_chance": 0.012,
                "material_chance": 0.015,
                "material_rank_chance": 0.010,
                "gem_chance": 0.080,
                "book_chance": 0.030,
                "weapon_chance": 0.015,
            }
        return {
            "feather_chance": float(row["feather_chance"]),
            "feather_rank_chance": float(row["feather_rank_chance"]),
            "material_chance": float(row["material_chance"]),
            "material_rank_chance": float(row["material_rank_chance"]),
            "gem_chance": float(row["gem_chance"]),
            "book_chance": float(row["book_chance"]),
            "weapon_chance": float(row["weapon_chance"]),
        }

    @staticmethod
    def _contribution_score(contribution: float) -> float:
        """奖励加成使用 sqrt(贡献度)，低贡献保留机会，高贡献收益更稳但不过度线性碾压。"""

        return sqrt(max(0.0, min(1.0, float(contribution))))

    @staticmethod
    def _good_loot_rolls(contribution_score: float, rank: int, influence_bonus: float = 0.0) -> int:
        """按 sqrt 贡献度决定珍贵战利品抽取次数。"""

        score = max(0.0, min(1.0, float(contribution_score)))
        bonus = max(0.0, min(0.08, float(influence_bonus)))
        rolls = 1
        if random.random() < min(0.95, score * 1.15 + bonus):
            rolls += 1
        if random.random() < min(0.75, max(0.0, score - 0.18) * 0.95 + bonus * 0.8):
            rolls += 1
        rank_bonus = 0.16 if rank == 1 else 0.10 if rank == 2 else 0.06 if rank == 3 else 0.0
        if random.random() < min(0.45, max(0.0, score - 0.45) * 0.65 + rank_bonus + bonus * 0.5):
            rolls += 1
        return min(4, rolls)

    def _public_battle_reward_bonus(self, client_id: str) -> float:
        """宗门影响力给公共战斗珍贵掉落小幅修正，不影响源石和经验。"""

        with self.db.transaction() as conn:
            return min(0.08, max(0.0, sect_direction_bonus_conn(conn, client_id, "influence") * 0.12))

    def _roll_good_loot(
        self,
        weight_type: str,
        contribution_score: float,
        rank: int,
        rates: dict[str, float],
        location_name: str = "",
        influence_bonus: float = 0.0,
        allow_feather: bool = True,
    ) -> dict[str, Any] | None:
        """从首领珍贵战利品池里随机一次。"""

        score = max(0.0, min(1.0, float(contribution_score)))
        rare_bonus_factor = 1.0 + max(0.0, min(0.08, float(influence_bonus)))
        feather_chance = self._feather_chance(weight_type, score, rank, rates, influence_bonus)
        if allow_feather and random.random() < feather_chance:
            return {"kind": "feather"}

        weights = [
            ("material", (max(rates["material_chance"], SEASONAL_LOW_CONTRIBUTION_FLOORS["material"]) + score * 0.10) * rare_bonus_factor),
            ("gem", max(rates["gem_chance"], SEASONAL_LOW_CONTRIBUTION_FLOORS["gem"]) + score * 0.10),
            ("book", (max(rates["book_chance"], SEASONAL_LOW_CONTRIBUTION_FLOORS["book"]) + score * 0.08) * rare_bonus_factor),
            ("weapon", (max(rates["weapon_chance"], SEASONAL_LOW_CONTRIBUTION_FLOORS["weapon"]) + score * 0.06) * rare_bonus_factor),
        ]
        if rank <= 3:
            weights[0] = ("material", weights[0][1] + rates["material_rank_chance"])

        total = sum(max(0.0, weight) for _kind, weight in weights)
        if total <= 0:
            return None
        roll = random.random() * total
        current = 0.0
        kind = "gem"
        for candidate, weight in weights:
            current += max(0.0, weight)
            if roll <= current:
                kind = candidate
                break

        if kind == "feather":
            return {"kind": "feather"}
        if kind == "material":
            item_id = random.choice(("kaikongqi", "xisuiye"))
            item = self.ring_item_def(item_id)
            return {"kind": "material", "item_id": item["ring_item_id"], "name": item["name"]} if item else None
        if kind == "gem":
            item = self._random_equipment_item("宝石")
            if not item:
                return None
            level = 1 + (1 if random.random() < min(0.25, score) else 0)
            return {"kind": "gem", "item_id": item["ring_item_id"], "name": item["name"], "level": level}
        if kind == "book":
            item = self._random_equipment_item("技能书")
            item = self.maybe_upgrade_extreme_book_item(item, location_name, 0.006)
            return {"kind": "book", "item_id": item["ring_item_id"], "name": item["name"]} if item else None
        return {"kind": "weapon", "drop": weapon_service.roll_weapon_drop()}

    @staticmethod
    def _feather_chance(
        weight_type: str,
        contribution_score: float,
        rank: int,
        rates: dict[str, float],
        influence_bonus: float = 0.0,
    ) -> float:
        """铭刻之羽独立判定概率；贡献和排名只做小幅修正。"""

        score = max(0.0, min(1.0, float(contribution_score)))
        bonus_factor = 1.0 + max(0.0, min(0.08, float(influence_bonus)))
        chance = max(rates["feather_chance"], SEASONAL_LOW_CONTRIBUTION_FLOORS["feather"])
        chance += score * SEASONAL_FEATHER_SCORE_BONUS
        if rank <= 3:
            chance += rates["feather_rank_chance"]
        if rank == 1 and weight_type == "高权重传统节日":
            chance += rates["feather_rank_chance"]
        return min(SEASONAL_FEATHER_CHANCE_CAP, chance * bonus_factor)

    def _random_equipment_item(self, category: str) -> dict[str, Any] | None:
        """随机纳戒物品，不包含开孔器。"""

        rows = self.db.fetch_all(
            """
            SELECT * FROM ring_item_defs
            WHERE category = ?
              AND ring_item_id != 'kaikongqi'
              AND ring_item_id != 'cuifengdan'
              AND ring_item_id NOT LIKE 'extreme_%'
            """,
            (category,),
        )
        return random.choice(rows) if rows else None

    def _participants(self, event_id: int) -> list[dict[str, Any]]:
        """读取首领贡献排行。"""

        return self.db.fetch_all(
            """
            SELECT * FROM seasonal_boss_participants
            WHERE event_id = ?
            ORDER BY damage DESC, updated_at ASC
            """,
            (event_id,),
        )

    def _contribution(self, damage: int, event: dict[str, Any]) -> float:
        """计算个人伤害占首领总血量的比例。"""

        max_hp = int(event.get("max_hp") or 0)
        if max_hp <= 0:
            return 0.0
        return max(0.0, min(1.0, int(damage) / max_hp))

    def _world_snapshot(self) -> dict[str, int]:
        """读取近期活跃生态，用于动态难度。"""

        players = self.db.fetch_all("SELECT * FROM players")
        if not players:
            return {"active_count": 1, "median_level": 1, "median_attack": 12, "median_hp": 120}
        levels = sorted(max(1, int(row["level"])) for row in players)
        attacks = []
        hps = []
        for row in players:
            weapon = self.equipped_weapon_row(str(row["client_id"]))
            flame_mult = self.equipped_flame_multiplier(str(row["client_id"]))
            attacks.append(max(1, floor((int(row["base_attack"]) + self.weapon_attack(weapon)) * flame_mult)))
            hps.append(max(1, int(row["max_hp"])))
        return {
            "active_count": max(1, len(players)),
            "median_level": int(median(levels)),
            "median_attack": int(median(attacks)),
            "median_hp": int(median(hps)),
        }

    @staticmethod
    def _estimate_challenge_damage(median_attack: int, level: int, boss_defense: int) -> int:
        """估算中位玩家单次挑战伤害。"""

        per_round = damage_after_defense(int(median_attack * 1.25 + level * 2.2), boss_defense)
        return max(25, per_round * 10)

    @staticmethod
    def _business_date() -> date:
        """取当前业务日日期。"""

        return (now() - timedelta(hours=DAY_RESET_HOUR)).date()

    def _boss_for_date(self, value: date) -> tuple[BossDef | None, str, str]:
        """选择指定日期的首领。

        节气、传统节日优先；普通日从代码里的“每日旧愿”首领池稳定轮换。
        """

        special = self._special_boss_for_date(value)
        if special[0]:
            return special
        boss_def = self._daily_boss_for_date(value)
        if boss_def:
            return boss_def, "每日旧愿", "每日旧愿"
        return None, "", ""

    def _special_boss_for_date(self, value: date) -> tuple[BossDef | None, str, str]:
        """只选择节气或传统节日首领；普通日不会兜底。"""

        choices: list[tuple[int, str, str, str]] = []
        term_key = _solar_term_key(value)
        if term_key:
            choices.append((1, "二十四节气", "普通节气", term_key))

        try:
            lunar = LunarDate.fromSolarDate(value.year, value.month, value.day)
            festival_key = LUNAR_FESTIVAL_DATES.get((lunar.month, lunar.day))
            if festival_key:
                weight = "高权重传统节日" if festival_key in HIGH_WEIGHT_FESTIVALS else "普通传统节日"
                priority = 3 if festival_key in HIGH_WEIGHT_FESTIVALS else 2
                choices.append((priority, "传统节日", weight, festival_key))
            tomorrow_lunar = LunarDate.fromSolarDate((value + timedelta(days=1)).year, (value + timedelta(days=1)).month, (value + timedelta(days=1)).day)
            if tomorrow_lunar.month == 1 and tomorrow_lunar.day == 1:
                choices.append((2, "传统节日", "普通传统节日", "chuxi"))
        except ValueError:
            pass

        for _priority, event_type, weight_type, boss_key in sorted(choices, key=lambda item: item[0], reverse=True):
            boss_def = ALL_BOSS_DEFS.get(boss_key)
            if boss_def:
                return boss_def, event_type, weight_type
        return None, "", ""

    def _daily_boss_for_date(self, value: date) -> BossDef | None:
        """普通日按日期稳定轮换一只每日旧愿首领。"""

        daily_bosses = [DAILY_BOSS_DEFS[key] for key in sorted(DAILY_BOSS_DEFS)]
        if not daily_bosses:
            return None
        return daily_bosses[value.toordinal() % len(daily_bosses)]

    def _next_boss_text(self) -> str:
        """展示下一次岁时情劫。"""

        start = self._business_date()
        for offset in range(1, 370):
            day = start + timedelta(days=offset)
            boss_def, event_type, _weight = self._boss_for_date(day)
            if boss_def:
                return f"下一次岁时情劫：{day.isoformat()} · {event_type} · {boss_def.name}，约 {offset} 天后。"
        return "暂未找到下一次岁时情劫。"

    def _format_status(self, event: dict[str, Any]) -> str:
        """格式化当前首领状态。"""

        closes = dt(event["closes_at"])
        left = max(0, int((closes - now()).total_seconds() // 60) + 1) if closes else 0
        extra = "\n今日为人间重节，旧愿尤深，铭刻之羽更易遗落。" if event["weight_type"] == "高权重传统节日" else ""
        location = str(event["location_name"])
        panel = T.panel()
        panel.section(f"今日岁时情劫·{event['boss_name']}")
        panel.line(f"{event['title']}，现于{location}。")
        panel.line(event["scene"])
        panel.line(f"等级：**{event['level']}**｜血量：**{event['hp']}/{event['max_hp']}**｜状态：{event['status']}")
        panel.line(f"剩余约 **{left}** 分钟｜挑战冷却 {SEASONAL_BOSS_CHALLENGE_COOLDOWN_MINUTES} 分钟｜每日最多 {SEASONAL_BOSS_MAX_CHALLENGES} 次")
        panel.line(f"{event['story']}{extra}")
        status = str(event["status"])
        if status == "开启":
            return T.attach(panel.render(), "发送：挑战首领<挑战首领>")
        if status in {"已击破", "已退去"}:
            return T.attach(panel.render(), "发送：首领奖励<首领奖励>")
        return panel.render()


service = SeasonalBossService(db)

__all__ = ["BOSS_DEFS", "SeasonalBossService", "service"]
