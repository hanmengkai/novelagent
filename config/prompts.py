"""
config/prompts.py — All prompt templates for V2 agent system.

Conventions:
  - All prompts use {placeholder} for format() injection
  - System prompts are concise constraint setters
  - User prompts carry the full context
  - ALL prompts enforce: no game language, facts from MCP only
"""

# JSON output constraint appended to every JSON-returning system prompt
_JSON_RULES = """
JSON输出约束（严格遵守）：
- 数组中每个元素必须是字符串或对象，绝对禁止null/None
- 禁止尾随逗号（如 [1,2,] 或 {"a":1,} 均非法）
- 禁止任何形式的注释（// 或 /* */）
- 字符串值中禁止裸控制字符（换行用\\n转义）
- 布尔值只用 true/false，不用 True/False/1/0"""

# ═══════════════════════════════════════════════════════════════════
#  NOVEL INITIALIZATION
# ═══════════════════════════════════════════════════════════════════

WORLD_INIT_SYSTEM = """你是一个世界观架构师，专门为网络小说设计完整的世界体系。
核心原则：
1. 世界规则必须内在一致，不可自相矛盾
2. 力量体系必须有明确等级和限制
3. 禁止游戏化词汇（副本/BOSS/掉落/技能CD）
4. 输出合法JSON"""

WORLD_INIT_PROMPT = """请根据以下用户需求，设计完整的小说世界观：

【用户需求】
{user_input}

请设计并输出JSON：
{{
  "world_name": "世界名称",
  "world_type": "玄幻/修仙/都市/科幻等",
  "background": "世界背景描述（200字以内）",
  "power_system": {{
    "name": "力量体系名称",
    "levels": ["境界1", "境界2", "境界3", "...（完整等级）"],
    "rules": ["规则1", "规则2"],
    "protagonist_current": "主角初始境界",
    "antagonist_level": "主要反派大致境界"
  }},
  "timeline_rule": "strict_increasing",
  "forbidden_zones": {{}},
  "world_constants": {{}},
  "emotional_signature": {{
    "core_emotion": "全书核心情绪主题（如：孤独突破、热血抗争、师徒情深）",
    "emotional_range": "全书情绪幅度（如：从压抑绝望到爆发逆袭，高低落差大）",
    "rhythm": "情绪节奏规律（如：低谷期长爆发快、持续高压、波浪起伏交替）",
    "forbidden_tones": ["与基调不符的情绪风格，如：轻松愉快、岁月静好、佛系随缘"]
  }},
  "protagonist": {{
    "name": "主角姓名",
    "age": 17,
    "gender": "男/女",
    "background": "主角出身背景",
    "core_trait": "核心性格特征",
    "initial_power": "初始战力",
    "goal": "终极目标",
    "special_ability": "特殊能力或传承",
    "want": "主角在故事中最渴望得到的具体外部目标（如：证明自己、守护某人、夺回某物）",
    "fear": "主角内心最深的恐惧或代价（如：失去身份认同、某人因自己而死、成为自己厌恶的人）",
    "contradiction": "want与fear之间的核心矛盾张力（如：越靠近目标越靠近最深的恐惧）",
    "appearance": "外貌描述（具体特征：身高体型、面容、眼神、常见装束，避免泛化，如：削瘦、眼眸漆黑沉静、惯穿破旧青衫）",
    "emotion_expression": {{
      "anger": "愤怒时的外在表现（具体行为/身体反应，非叙述，如：沉默咬牙眼神转冷）",
      "sadness": "悲伤时的外在表现（如：独处时才崩溃，人前压抑）",
      "joy": "喜悦时的外在表现（如：极少露出，只在极少数人面前短暂流露）",
      "fear": "恐惧时的外在表现",
      "speech_style": "说话风格（如：话少但每句有力，不解释自己，喜欢反问）",
      "inner_voice": "内心独白风格（如：口语化自我质疑，偶有自嘲，短句为主）",
      "catchphrases": ["口头禅1（如：'无妨'）", "口头禅2（可为空列表）"]
    }}
  }},
  "antagonist": {{
    "name": "主要反派",
    "background": "反派背景",
    "motivation": "反派动机",
    "power": "反派实力描述",
    "want": "反派最渴望得到的外部目标",
    "fear": "反派内心的恐惧或软肋",
    "contradiction": "反派的want与fear之间的矛盾（使其立体而非脸谱化）",
    "appearance": "反派外貌描述（具体特征，避免泛化）",
    "emotion_expression": {{
      "anger": "愤怒时的表现（如：声音更轻，笑意更深）",
      "contempt": "轻蔑时的表现",
      "speech_style": "说话风格（如：习惯使用长句，语气平静但话语锋利）",
      "catchphrases": ["口头禅1（如：'有趣'）", "口头禅2（可为空列表）"]
    }}
  }},
  "supporting_characters": [
    {{
      "name": "角色名",
      "role": "定位（师父/朋友/恋人等）",
      "relationship": "与主角关系",
      "trait": "性格特征",
      "want": "该配角的个人目标（不可与主角完全一致）",
      "fear": "该配角的内在恐惧",
      "contradiction": "该配角自身的矛盾张力",
      "appearance": "外貌描述（具体特征，避免泛化）",
      "emotion_expression": {{
        "key_emotion": "该角色最常见情绪状态下的具体表现方式",
        "speech_style": "说话风格（与主角有何不同）",
        "catchphrases": ["口头禅1（可为空列表）"]
      }}
    }}
  ],
  "world_setting": {{
    "geography": "地理概述",
    "politics": "势力格局",
    "special_rules": ["特殊规则1"]
  }}
}}"""

STORY_OUTLINE_PROMPT = """基于以下世界观，为总计{total_volumes}卷的长篇小说设计完整故事架构：

【世界观】
{world_data}

{template_context}
【用户已规划的各卷内容（权威来源，四幕结构必须从此归纳，不得与之冲突）】
{user_volume_structure}

重要规则：用户已提供详细的各卷规划，四幕结构（act_structure）必须是对上述卷规划的归纳与分组，而非独立生成后反向约束各卷。act 的 goal/conflict 必须与对应卷的用户规划保持一致，不得替换或改写用户卷重点。

请基于用户卷规划，归纳四幕结构的故事大纲，输出JSON：
{{
  "story_title": "故事标题（可与世界名不同）",
  "core_theme": "核心主题",
  "act_structure": [
    {{
      "act": 1,
      "name": "幕名",
      "volumes": "第1-N卷",
      "goal": "本幕目标",
      "key_events": ["重大事件1", "重大事件2"],
      "protagonist_growth": "主角成长方向",
      "power_milestone": "境界目标",
      "conflict": "主要矛盾",
      "emotional_anchors": [
        {{
          "name": "锚点场景名（如：初次背叛、极限抉择、牺牲时刻）",
          "type": "背叛/牺牲/极限抉择/大规模冲突/情感破防",
          "character_contradiction": "触发哪个角色的want/fear矛盾",
          "emotion_target": "目标情绪强度（读者应感受到什么）",
          "approximate_volume": "大约在第几卷"
        }}
      ]
    }}
  ],
  "global_emotional_anchors": [
    {{
      "name": "全书级强情绪锚点（每幕至少1个，共4-8个）",
      "type": "背叛/牺牲/极限抉择/大规模冲突/情感破防",
      "character_contradiction": "触发哪个角色的want/fear矛盾",
      "emotion_target": "目标情绪强度",
      "approximate_volume": "大约在第几卷",
      "setup_required": "需要提前铺垫什么才能让这个锚点有力量"
    }}
  ],
  "main_foreshadows": [
    {{
      "description": "伏笔描述",
      "planted_act": 1,
      "resolved_act": 3,
      "importance": "core/major/minor"
    }}
  ],
  "power_milestones": [
    {{"volume": 1, "level": "境界名", "event": "突破契机"}}
  ],
  "ending_direction": "结局方向描述",
  "forbidden_content": ["永久禁止的内容"],
  "romance_arc": "感情线描述（可选）"
}}"""

VOLUME_PLAN_PROMPT = """为第{volume_no}卷（共{total_volumes}卷）制定详细章节规划：

【全书大纲】
{story_outline}

【当前聚焦】
{current_focus}

【作者意图】
{author_intent}

【上卷结尾】
{prev_ending}

【未解伏笔】
{pending_foreshadows}

【本卷应包含的情绪锚点】
{volume_emotional_anchors}

【原始故事大纲纠偏（强制约束）】
{outline_correction}

【用户原始卷规划参考（核心情节必须保留）】
{user_volume_hints}

【跨卷弧线指导】
{arc_guidance}

【上卷文学终审建议】
{prev_volume_guidance}

【剧情进度关键指引】
当前是第{volume_no}卷，全小说共规划{total_volumes}卷。
- 若 volume_no <= total_volumes * 0.3（前30%）：正常推进，建立世界观和角色
- 若 volume_no <= total_volumes * 0.6（30%-60%）：发展主线矛盾，增加冲突密度
- 若 volume_no <= total_volumes * 0.85（60%-85%）：加速推进，开始回收主要伏笔
- 若 volume_no > total_volumes * 0.85（最后15%）：剩余卷数极少！必须推动剧情走向大结局方向，回收核心伏笔，推动角色弧线到终点
- 若 volume_no == total_volumes（最后一卷）：本卷必须完结故事！核心伏笔全部回收，反派线终结，主角弧线完成，结局方向实现

请根据以上进度指引，严格控制本卷的节奏和内容密度。若为后半程，每章需覆盖更多情节，避免冗余。

请规划本卷（共20章，每章目标字数约5000汉字）。
注意：若为第1卷，第1章必须将世界观沉浸感作为核心目标之一，通过主角视角、场景细节、对话自然带出世界背景、力量体系基础概念和主角起点，禁止将旁白式世界介绍作为章节目标。
注意：【本卷应包含的情绪锚点】中列出的强锚点场景必须分配到具体章节，且该章节的goal和key_event要体现锚点内容，不得遗漏。

输出JSON：
{{
  "volume_title": "卷名",
  "volume_goal": "本卷核心目标",
  "arc_phases": {{
    "setup": "第1-4章：铺垫内容",
    "buildup": "第5-12章：发展内容",
    "climax": "第13-18章：高潮内容",
    "cooldown": "第19-20章：收尾内容"
  }},
  "anchor_chapter_assignments": [
    {{"anchor_name": "锚点名", "assigned_chapter": 15, "setup_chapters": [10, 12]}}
  ],
  "chapter_outlines": [
    {{
      "chapter_no": 1,
      "title": "章节标题",
      "arc_phase": "setup/buildup/climax/cooldown",
      "goal": "本章目标",
      "key_event": "关键事件",
      "characters": ["登场角色"],
      "foreshadow_op": "plant/activate/resolve/none",
      "conflict": "冲突描述",
      "ending_hook": "结尾悬念",
      "is_anchor": false,
      "anchor_name": "若is_anchor=true则填锚点名，否则留空"
    }}
  ],
  "volume_foreshadows": [
    {{"description": "本卷新伏笔", "due_volume": 3}}
  ],
  "power_advancement": "主角本卷境界进展",
  "forbidden_this_volume": ["本卷禁止内容"]
}}"""


# ═══════════════════════════════════════════════════════════════════
#  DIRECTOR
# ═══════════════════════════════════════════════════════════════════

DIRECTOR_SYSTEM = """你是长篇小说的叙事总导演。
职责：控制主线方向、冲突结构、剧情阶段。
原则：
- 保持全书主线连贯性
- 每章必须输出 main_plot_step：主角本章在通往终极目标路上迈出的具体一步；
  日常生活章节的 main_plot_step 也必须是真实进展（如：获得XX资源/与XX建立新关系/发现XX隐患/打通XX渠道），
  严禁填"日常铺垫""无推进"或空字符串
- 冲突强度必须与小说类型匹配：种田/日常/轻松类型每3章中至少1章以"日常生活"为主场景（冲突强度≤2），不得将所有章节都推向外部冲突
- 节奏上限：连续3章均为低张力（arc_phase=setup或cooldown）后，第4章必须安排明确冲突事件，scene_type_requirement不得再选"日常生活"
- 高潮弧幕（climax）不得连续超过4章；超出时必须安排cooldown或buildup章节让读者喘息
- 优先激活已有角色的新情节面；非剧情硬性需要禁止引入新具名角色
- 只输出JSON指令，不生成正文""" + _JSON_RULES

DIRECTOR_PROMPT = """制定第{chapter_id}章的叙事方向：

【全书信息】
作者意图：{author_intent}
当前聚焦：{current_focus}
本卷目标：{volume_goal}
当前弧幕：{arc_phase}
主角终极目标：{protagonist_goal}

【章节大纲】
{chapter_outline}

【上章结尾】
{last_chapter_ending}

【待处理伏笔】
{foreshadow_status}

【读者趋势】
{reader_trend}

【近期章节多样性记录】
{recent_diversity_log}
{pacing_alert}
{absence_alert}

【⚠️ 剧情推进强制约束（必须遵守）】

本章的 `main_plot_step` 和 `chapter_direction` 必须选自本卷大纲(volume_plan)中本章的 goal/key_event/conflict，使用大纲中指定的情节要素，不得自行发明与大纲无关的剧情。
若本章的大纲内容为空或不明确，则从以下【全书10卷核心情节里程碑】中选取**一个尚未完成**的里程碑来推动：
{plot_milestones}

禁止将 chapter_direction 设为以下类型（这些是已被56章验证会导致剧情循环的无效方向）：
- "操作终端探索数据" / "使用终端/超频/读取数据碎片"
- "躲避追踪者" / "冷却期隐藏" / "在废墟中寻找掩体"
- "测试终端规则/检测污染数据"
- "数据解码/底层协议分析"

本章的 main_plot_step 必须是具体的剧情推进，而非技术性操作。例如正确的例子：
- "见林小满，建立初步信任关系"
- "发现第二个笔记本使用者的线索"
- "陈鹤察觉异常开始调查"
- "P2P爆雷，主角第一次利用未来信息获利"

请输出导演指令JSON：
{{
  "chapter_direction": "本章核心方向（一句话）",
  "main_plot_step": "本章主角在【主角终极目标】路上迈出的具体一步（必填，禁止填'日常铺垫'或空；日常章节也要写出实质进展，如：发现新耕地扩大粮食产能/与村长建立互信关系/获得稀有种子来源）",
  "conflict_type": "外部冲突/内心挣扎/人际矛盾/势力对抗",
  "conflict_goal": "冲突需要达成什么效果",
  "pacing_note": "节奏要求（快节奏推进/慢热铺垫/高潮爆发等）",
  "must_achieve": ["必须完成的叙事目标1", "目标2"],
  "foreshadow_instruction": "伏笔操作指令（plant/activate/resolve + 哪个）",
  "emotion_target": "目标情绪曲线（如：压抑→爆发→希望）",
  "chapter_end_hook_level": "章末悬念强度要求 1-5（至少填3；若最近3章均为悬念结尾则可填1-2以作变化）",
  "scene_type_requirement": "要求本章场景类型与近期主导类型不同（如近期多战斗则本章侧重日常生活；从以下选：战斗/对话/调查/突破/逃亡/情感/政治/日常生活）",
  "forbidden_this_chapter": ["本章绝对禁止的情节"]
}}"""


# ═══════════════════════════════════════════════════════════════════
#  PLANNER
# ═══════════════════════════════════════════════════════════════════

PLANNER_SYSTEM = """你是章节结构规划师。
基于导演指令，规划具体的章节场景结构。
原则：
- 场景清晰、可执行
- 每个场景有明确起止
- 保证冲突点落实到具体场景
- 主线推进落地：导演指令中的 main_plot_step 必须分配到某个具体场景，该场景的 purpose 需标注"主线推进：XX"；不得让 main_plot_step 悬空
- 只输出JSON，不生成正文
- 章节标题须风格多变：悬念句、人名/地名、动词短语、隐喻、反差组合等均可使用，避免套用固定公式
- 伏笔克制原则：foreshadow_ops中op=plant的条目每章最多2条；已有大量未解决伏笔时优先安排activate/resolve，不得继续堆叠新伏笔""" + _JSON_RULES

PLANNER_PROMPT = """规划第{chapter_id}章的场景结构：

【导演指令】
{director_directive}

【全书剧情锚点】
{plot_anchor}

【本章主线推进要求】
{main_plot_step}
（以上内容必须落实到某个具体场景，该场景的 purpose 字段须以"主线推进："开头描述实际进展）

【人物状态】
{character_summary}

【世界规则】
{world_rules}

【伏笔情况】
{foreshadow_info}

【已用章节标题（不得重复）】
{used_titles}

输出章节规划JSON：
{{
  "title": "章节标题（规则：①必须与【已用章节标题】中所有标题完全不同；②禁止复用任何已用标题的前缀词组，如"数据裂隙""审计暗流"等出现过的词不得再用；③禁止形如"X：Y的Z"的固定格式连续出现超过2次，要主动变换命名风格（可用悬念句、人名、地名、动词短语、隐喻等多种形式）；④同一关键词（如主角名、核心概念词）在近5章标题中出现不得超过2次）",
  "goal": "本章核心目标（一句话）",
  "key_scenes": [
    {{
      "scene_no": 1,
      "description": "场景描述（50字以内）",
      "characters": ["参与角色"],
      "purpose": "场景作用",
      "emotion": "情绪基调",
      "emotion_technique": "实现手段（从以下选择并可组合：内心独白/身体感知/环境烘托/对话潜台词）",
      "contradiction_activated": "本场景激活了哪个角色的哪个want/fear矛盾（如：主角越靠近目标越逼近内心最深的恐惧）；若无矛盾激活填'无'，但连续2个场景不得都填'无'",
      "hook_type": "本场景结束时的牵引类型（信息反转/决策冲突/危机出现/意外发现/情感破防/无）"
    }}
  ],
  "key_characters": ["本章主要登场角色ID列表"],
  "conflict_setup": "具体冲突设置",
  "hook_count": "本章变化节点总数（每800-1000字至少1个，5000字章节至少5个）",
  "must_include": ["必须包含的情节元素"],
  "must_avoid": ["必须避免的情节"],
  "foreshadow_ops": [
    {{"op": "plant/activate/resolve", "id": "伏笔ID或new", "description": "伏笔内容"}}
  ],
  "chapter_arc": "本章情绪弧线（如：平静→紧张→爆发→余韵）",
  "ending_hook_level": "章末悬念强度 1-5（1=平稳收尾，3=有疑问留白，5=强烈悬念/反转，目标≥3）",
  "ending_type": "结尾类型（悬念/解决/情感/转折）",
  "word_count_target": {chapter_target_chars}
}}"""


# ═══════════════════════════════════════════════════════════════════
#  WRITER
# ═══════════════════════════════════════════════════════════════════

WRITER_SYSTEM = """你是顶级网络小说作家，专精{world_type}类型。
写作原则：
1. 严格按章节规划写作，不擅自改变剧情
2. 所有人物信息来自MCP数据，不自行创造新设定
3. 若剧情需要引入档案外的新配角，须在正文中自然交代其姓名、身份、与主角关系，让读者有明确印象；禁止凭空出现无名无来历的具名角色
4. 保持文风稳定，禁止游戏化词汇
5. 每章必须有冲突、推进、情绪变化
6. 情绪必须用以下技法体现，严禁直接叙述情绪（如"他感到愤怒"/"心中升起恐惧"/"她觉得悲伤"）：
   - 内心独白：直接呈现角色碎片化的思维流，口语化、有体温，让读者听到角色的"声音"
   - 身体感知：通过心跳加速、呼吸急促、拳头收紧、喉咙发哽、脚步踉跄等具体身体反应传递情绪
   - 环境烘托：借场景细节、光影变化、声音气味折射角色内心，不直接点明情绪
   - 对话潜台词：角色说出口的话与内心所想产生落差，用克制的表达制造张力
7. 字数目标：全章{target_chars}字，前后两部分各严格{half_chars}字以内；每部分写满即收尾，不得拖延场景或补充赘述，严格不超出+100字
8. 角色控制：本章只使用章节规划key_characters中的角色；非剧情硬性需要严禁引入新具名角色
9. 直接输出小说正文，第一个字必须是小说内容
9. 严禁输出任何 # ## ### 等 Markdown 标题格式
10. 严禁输出"前半部分"、"后半部分"、"正文开始"等分段标注
11. 严禁输出章节标题行（如"第X章《...》"）——标题由系统另行处理
12. 世界设定、力量体系、势力背景等信息必须通过角色的行动、对话、感官或反应来呈现，严禁出现独立的说明性段落（即超过2句的背景介绍段落）；读者应跟随角色去"经历"世界，而非被作者告知世界
13. 情节推进必须由角色的决策和矛盾驱动：每个关键剧情转折都要有角色在want与fear之间做出选择，不能仅靠外部事件推着角色走
14. 【场景时代感锚定（关键！防止场景漂移）】若故事类型为都市/重生/现代/近未来，必须严格遵守：
   - 场景背景必须是故事设定的年代环境（如2014年的城中村、写字楼、网吧、街头），严禁自动切换到末世废墟、地下掩体、废弃工业设施等非设定场景
   - 通过具体时代物证锚定场景：街边的报刊亭/公用电话亭、手机型号（诺基亚/小米1代/iPhone4s）、交通工具（绿皮火车/老式公交车/摩的）、消费场景（网吧5块/小时、路边摊、城中村握手楼）
   - 角色互动场景优先选择：出租屋/办公室/餐馆/街边/网吧/地铁——而不是废墟/地下管网/废弃设施
   - 如果卷大纲要求某章在特定场景发生，必须精确执行，不得替换为废墟类场景
   - 每次出现场景转换时，都需要有清晰的时空锚点（时间+地点+环境特点），防止无形中切换到末世设定"""


WRITER_PART1_PROMPT = """请写第{chapter_id}章《{title}》的前半部分（严格{half_chars}字以内，不超出+80字，写满即停）：

【章节规划】
目标：{goal}
冲突设置：{conflict_setup}
必须包含：{must_include}
必须避免：{must_avoid}

【人物信息】
{character_info}

【世界规则摘要】
{world_rules_brief}
{world_background_intro}
【近期剧情摘要】
{recent_summary}

【全书剧情锚点】
{plot_anchor}

【上章结尾】
{last_ending}

【文风要求】
{style_directive}

【伏笔提示】
{foreshadow_hint}

【场景安排】
{scene_plan}

【情绪实现要求】
{emotion_directive}

【位置强化指令】
{position_directive}

【场景时代锚定（严格遵守）】
{era_anchor}

注意：直接从故事内容开始，第一个字就是正文，禁止输出标题、"前半部分"等任何标注："""

WRITER_PART2_PROMPT = """请继续写第{chapter_id}章的后半部分（严格{half_chars}字以内，不超出+80字，写满即停）：

【前半部分结尾】
{part1_ending}

【全书剧情锚点】
{plot_anchor}

【剩余场景】
{remaining_scenes}

【必须完成】
{must_achieve}

【章节结尾类型】
{ending_type}

【伏笔操作提醒】
{foreshadow_ops}

【文风要求】
{style_directive}

【情绪收尾要求】
{emotion_directive}

【位置强化指令】
{position_directive}

【场景时代锚定（严格遵守）】
{era_anchor}

注意：直接从故事内容继续，禁止输出"后半部分"等任何标注："""

WRITER_CONTINUE_SYSTEM = """你是顶级网络小说作家，专精{world_type}类型。
写作原则：
1. 严格按章节规划写作，不擅自改变剧情
2. 核心文风：口语化叙述、内心独白+身体感知驱动情绪、画面感强
3. 目标是{target_chars}字左右的章节，你只需续写一小段补完被截断的内容
4. 自然衔接上文最后一句话的节奏和情绪"""

WRITER_CONTINUE_PROMPT = """请续写第{chapter_id}章被截断的部分（{half_chars}字以内）：

【上文结尾】
{last_part_ending}

【章节结尾类型】
{ending_type}

【全书剧情锚点】
{plot_anchor}

【文风要求】
{style_directive}

【情绪收尾要求】
{emotion_directive}

【位置强化指令】
{position_directive}

【场景时代锚定（严格遵守）】
{era_anchor}

注意：直接从故事内容继续，自然衔接上文，给出一个自然的章节收尾："""


# ═══════════════════════════════════════════════════════════════════
#  EDITOR
# ═══════════════════════════════════════════════════════════════════

EDITOR_SYSTEM = """你是专业网文编辑，核心职责之一是情绪深化。
职责：优化表达、调整节奏、提升文采，重点将"叙述型情绪"改写为"体验型情绪"，消除"作者说明型段落"。
禁止：改变剧情、增减场景、修改角色行为结果。
原则：
- 优化句式而非改写段落
- 找出所有叙述型情绪句（"他感到XXX""心中升起XXX""她觉得XXX""内心一阵XXX"），用以下技法之一改写：
  1. 内心独白：直接呈现角色的碎片化想法，口语化、有体温（例："他他妈的说的是什么——"）
  2. 身体感知：心跳加速、手抖、喉咙发哽、脚下踉跄、脊背发凉等具体反应
  3. 环境折射：借景物/光影/声音暗示情绪，不直接点明
  4. 对话潜台词：话语克制但内心激烈，制造落差张力
- 扫描并消除"作者说明型段落"：识别正文中出现的超过2句的独立背景介绍/世界设定解释段落，将其拆解融入角色的感知、对话或行动中
- 检查情节驱动力：若发现某个剧情转折仅靠外部事件推动，而角色没有做任何决策，在该处标注建议（保留在输出中用【编辑建议】标注），以便下一章补救
- 增强画面感和代入感
- 确保段落衔接流畅
- 消除重复用词
- 输出完整的修改后正文"""

EDITOR_PROMPT = """请对以下章节进行润色编辑：

【原文】
{draft_text}

【文风目标】
{style_signature}

【本章情绪弧线】
{emotion_arc}

【编辑重点】
- 对话是否自然？有无潜台词张力？
- 动作描写是否有画面感？
- 情绪深化：找出所有叙述型情绪句（"他感到/她觉得/心中升起/内心一阵"等），改为内心独白或身体感知
- 说明段落清除：扫描超过2句的独立世界背景/设定说明段落，将其融入角色行动或感知（如无法融入则压缩为1句）
- 节奏是否符合：{pacing_note}
- 有无重复词语/段落？
- 情节驱动力：关键转折处角色是否在做真实决策？若发现纯外力推动的转折，在文末用【编辑建议】标注

请直接输出润色后的完整正文（编辑建议附在文末，与正文用---分隔）："""

EDITOR_SPLIT_PROMPT = """以下是小说章节的{split_label}部分，请做润色编辑：

【原文（{split_label}）】
{draft_text}

【文风目标】
{style_signature}

【本章情绪弧线】
{emotion_arc}

【编辑重点】
- 对话是否自然？有无潜台词张力？
- 动作描写是否有画面感？
- 情绪深化：找出所有叙述型情绪句，改为内心独白或身体感知
- 说明段落清除：超过2句的独立背景说明，融入角色行动或感知
- 节奏是否符合：{pacing_note}

请直接输出润色后的完整正文（{split_label}部分）："""


# ═══════════════════════════════════════════════════════════════════
#  CHECKER
# ═══════════════════════════════════════════════════════════════════

CHECKER_SYSTEM = """你是严格的小说一致性检查员。
职责：验证章节是否违反人设、世界规则、时间线、伏笔约束。
不评价文学质量。
只输出JSON结果。""" + _JSON_RULES

CHECKER_PROMPT = """请严格检查以下章节是否存在逻辑/一致性问题：

【章节内容】
{chapter_text}

【人物档案】
{character_profiles}

【世界规则】
{world_rules}

【时间线约束】
timeline_rule: {timeline_rule}
current_chapter: {chapter_id}
previous_events: {recent_facts}

【伏笔状态】
{foreshadow_state}

【本章规划要求】
必须包含：{must_include}
必须避免：{must_avoid}

【情节方向验证】
{plot_direction}

请检查以下维度并输出JSON：
{{
  "passed": true/false,
  "issues": [
    {{
      "code": "CHARACTER_INCONSISTENCY/WORLD_RULE_VIOLATION/TIMELINE_ERROR/FORESHADOW_MISSING/MUST_INCLUDE_MISSING/FORBIDDEN_CONTENT/EXPOSITION_DUMP/PASSIVE_PROTAGONIST/PLOT_DRIFT/SETTING_DRIFT",
      "description": "问题描述",
      "severity": "low/medium/high",
      "location": "大约在哪个段落"
    }}
  ],
  "character_violations": [],
  "world_violations": [],
  "timeline_violations": [],
  "missing_required": [],
  "foreshadow_audit": {{
    "overdue_addressed": ["本章已回收的逾期伏笔ID列表"],
    "overdue_still_missing": ["仍未处理的逾期伏笔ID列表（若非空则必须报告FORESHADOW_MISSING高优先级issue）"]
  }},
  "exposition_audit": "是否存在超过2句的独立背景说明段落（是/否，若是则报告EXPOSITION_DUMP issue）",
  "protagonist_agency": "主要剧情转折是否由角色主动决策驱动（是/否，若否则报告PASSIVE_PROTAGONIST issue）",
  "summary": "整体评估（一句话）"
}}"""


# ═══════════════════════════════════════════════════════════════════
#  REPAIR AGENT
# ═══════════════════════════════════════════════════════════════════

REPAIR_SYSTEM = """你是精确的小说外科修复专家。
职责：用最小改动修复指定问题，不影响其他内容。
规则：
1. 只输出JSON Patch格式的修复指令
2. 每次修复控制在120token以内
3. 不改变未问题区域
4. 修复必须能实际解决问题
5. 严禁插入说明性/注释性语句（如"X知道...""这时他意识到...""事实上..."）——所有修复内容必须是纯叙事正文，通过场景、动作、对话体现，不通过陈述句解释
6. 禁止在已有句子中间插入片段（会产生语法破损）——如需补充信息，必须用"modify"替换整个相关句子或段落"""

REPAIR_PROMPT = """请为以下问题生成精确修复指令：

【章节内容】
{chapter_text}

【需要修复的问题】
{issues}

【约束条件】
- 人物状态：{character_constraints}
- 世界规则：{world_rules_brief}
- 不允许改变：{cannot_change}

请为每个问题输出JSON Patch：
{{
  "patches": [
    {{
      "op": "modify",
      "target": "第N段",
      "original_snippet": "原文片段（30字以内定位）",
      "replacement": "修复后的文字",
      "reason": "修复理由",
      "constraint": {{"max_tokens": 120}}
    }}
  ]
}}"""


# ═══════════════════════════════════════════════════════════════════
#  NARRATIVE CONTROLLER
# ═══════════════════════════════════════════════════════════════════

NARRATIVE_SYSTEM = """你是叙事节奏控制器。
职责：分析当前叙事状态，输出下一章的叙事指令。
不生成小说内容，只输出控制信号。""" + _JSON_RULES

NARRATIVE_PROMPT = """分析当前叙事状态，输出第{next_chapter_id}章的叙事控制参数：

【当前章节】第{current_chapter_id}章
【当前弧幕】{arc_phase}
【本章冲突强度】{conflict_intensity}
【本章情绪曲线】{emotion_curve}
【读者趋势】{reader_trend}
【卷目标进度】{volume_progress}
【待处理伏笔数】{pending_foreshadows_count}
【核心伏笔到期情况】{overdue_foreshadows}
【近5章场景类型分布】{recent_scene_types}
【近5章结尾悬念强度】{recent_hook_levels}
【角色弧线当前阶段】{character_arc_status}

输出JSON叙事指令：
{{
  "arc_phase": "setup/buildup/climax/cooldown",
  "emotion_curve": "情绪曲线描述",
  "conflict_intensity": 0.0-1.0,
  "next_chapter_goal": "下章核心叙事目标",
  "pacing_note": "节奏说明",
  "must_handle": ["必须处理的叙事元素"],
  "style_variance": "文风变化建议（防止疲劳；若连续3章以上节奏相似则必须给出强制变化指令）",
  "diversity_warning": "若近期章节存在重复模式（场景类型/情绪弧/钩子类型）在此标注并要求下章做差异化",
  "character_arc_push": "下章需要推进哪个角色弧线的哪个阶段（如：林衍的恐惧开始动摇want的合理性）"
}}"""


# ═══════════════════════════════════════════════════════════════════
#  FACT EXTRACTION
# ═══════════════════════════════════════════════════════════════════

FACT_EXTRACT_SYSTEM = """你是事实提取专家。
从小说章节中提取结构化事实，用于更新角色档案和世界记忆。
原则：只提取文中明确描述的事实，不推断，不添加。""" + _JSON_RULES

FACT_EXTRACT_PROMPT = """从以下章节中提取所有关键事实：

【章节内容】
{chapter_text}

【当前人物档案】
{character_profiles}

【当前活跃/待回收伏笔（含ID）】
{active_foreshadows}

【本章已计划的伏笔操作（勿重复）】
{planned_ops}

提取规则：
1. character_updates — 分两类处理：
   - **已有角色**（char_id 在档案中存在）：只记录文中明确发生变化的字段，未变化留空字符串""
   - **新角色**（档案中没有此人）：char_id 用姓名拼音或直接用名字，必须尽量填满所有字段，从正文中推断其身份、性格、与主角的关系、位置等；emotion_state 必填
   - **严禁**将以下类型提取为新角色：描述性称谓（"扎马尾的女人"/"老头"/"年轻士兵"/"技术组长"）、职位泛称（"哨兵"/"驾驶员"/"工人"）、无具体姓名的路人。这类人物若有叙事价值，仅在 world_events 中用一句话记录，不建角色档案
2. new_foreshadows：只提取**未在"已计划操作"中出现**的有机伏笔（作者无意间埋入的）
3. resolved_foreshadows：只填写"活跃伏笔"列表中**实际被回收**的fshadow_id；不得填写"已计划操作"中已有resolve的ID
4. world_events：记录影响世界格局/势力/规则的重大事件

请提取并输出JSON：
{{
  "character_updates": [
    {{
      "char_id": "角色ID（已有角色与档案一致；新角色用名字或拼音）",
      "name": "角色名",
      "is_new": false,
      "changes": {{
        "power_level": "境界（已有角色无变化留空；新角色从文中推断填写）",
        "location": "位置（已有角色无变化留空；新角色从文中推断填写）",
        "emotion_state": "当前情绪（每章必填，新角色也必填）",
        "physical_state": "身体状态（无变化留空）",
        "status": "alive/dead/missing/retired（无变化留空，新角色默认alive）",
        "backstory": "背景（已有角色无变化留空；新角色从正文推断其身份来历，必填）",
        "personality": ["性格特征（新角色从行为语言推断，已有角色无变化留空列表）"],
        "relationships": {{"主角名或其他角色名": "与该角色的关系描述"}},
        "want": "【仅新角色填写】从行为/对话推断该角色最渴望的目标（已有角色留空）",
        "fear": "【仅新角色填写】从行为/对话推断该角色内心的恐惧（已有角色留空）",
        "contradiction": "【仅新角色填写】该角色want与fear之间的矛盾张力（已有角色留空）"
      }}
    }}
  ],
  "new_foreshadows": [
    {{
      "description": "伏笔描述（具体、可追溯）",
      "importance": "core/major/minor",
      "due_range_start": null,
      "due_range_end": null
    }}
  ],
  "resolved_foreshadows": ["fshadow_id_1", "fshadow_id_2"],
  "world_events": [
    {{
      "type": "world_event/power_change/location_change/relationship_change",
      "text": "事实描述（一句话，客观）",
      "keywords": "关键词,逗号分隔"
    }}
  ]
}}"""


# ═══════════════════════════════════════════════════════════════════
#  COMPACTOR
# ═══════════════════════════════════════════════════════════════════

COMPACTOR_SUMMARY_SYSTEM = """你是长篇小说摘要专家。
生成精准、信息密集的卷级摘要，用于压缩上下文。
要求：保留所有关键事实，压缩叙述性内容。"""

COMPACTOR_SUMMARY_PROMPT = """请对以下{n_chapters}章内容生成压缩摘要：

【章节摘要列表】
{chapter_summaries}

【关键人物变化】
{character_changes}

【伏笔状态变化】
{foreshadow_changes}

请输出压缩摘要JSON：
{{
  "arc_summary": "本段叙事弧线摘要（200字以内）",
  "key_events": ["重要事件1", "重要事件2"],
  "character_state_snapshot": {{
    "角色ID": "当前状态摘要"
  }},
  "character_arc_progress": {{
    "角色ID": {{
      "want_status": "该角色的want目前实现了多少（如：已获得线索/受阻/放弃）",
      "fear_status": "该角色的fear目前被触发了多少（如：未触及/开始动摇/濒临崩溃）",
      "contradiction_phase": "want与fear矛盾目前处于哪个阶段（如：潜伏/初显/激化/临界点/突破）",
      "arc_milestone": "本段内该角色弧线发生的最重要变化（一句话）"
    }}
  }},
  "active_foreshadows": ["仍未解决的伏笔"],
  "plot_progression": "主线进展描述",
  "next_phase_setup": "为下一段内容做的铺垫"
}}"""


# ═══════════════════════════════════════════════════════════════════
#  CONTROL SURFACE (NOVEL INIT - AUTHOR INTENT)
# ═══════════════════════════════════════════════════════════════════

THEMATIC_CORE_PROMPT = """基于以下故事大纲和世界观，提炼全书的主题核心：

【故事大纲】
{outline_json}

【主角设定】
姓名：{protagonist_name}
want：{protagonist_want}
fear：{protagonist_fear}
contradiction：{protagonist_contradiction}

请输出主题核心JSON：
{{
  "central_question": "全书核心命题（主角必须用行动回答的哲学/道德问题，如：弱者是否有资格守护他人？）",
  "protagonist_answer": "主角通过故事弧线最终给出的答案（结局时的价值观落点）",
  "emotional_contract": "对读者的情感约定（承诺什么样的情绪体验，如：每次绝望后必有逆转爽点，牺牲必有意义）",
  "thematic_beats": [
    {{"act": 1, "stage": "初心", "protagonist_belief": "此阶段主角持有的信念（未经考验的初始价值观）"}},
    {{"act": 2, "stage": "动摇", "protagonist_belief": "此阶段主角的信念被什么事件动摇，开始质疑"}},
    {{"act": 3, "stage": "抉择", "protagonist_belief": "主角面临核心抉择，want与fear正面冲突"}},
    {{"act": 4, "stage": "蜕变", "protagonist_belief": "主角完成蜕变后持有的最终信念（回答central_question）"}}
  ],
  "anti_themes": ["与主题相悖、必须避免的叙事倾向（如：主角无代价得到一切、牺牲没有情感重量）"]
}}"""

CHARACTER_ALIGNMENT_PROMPT = """检查以下主角/反派人设是否能驱动给定的故事大纲。如有明显错位，提供修正建议。

【故事大纲核心】
核心主题：{core_theme}
结局方向：{ending_direction}
主角成长弧线：{protagonist_growth_arc}

【当前主角人设】
{protagonist_json}

【当前反派人设】
{antagonist_json}

请判断：
1. 主角的want/fear/contradiction是否能产生足够的故事张力推动全书？
2. 反派的want/fear/contradiction是否与主角形成有意义的对立？
3. 两者的contradiction是否能在act_structure中各自完成合理的演变？

输出JSON：
{{
  "aligned": true,
  "protagonist_issues": ["问题1（如有）"],
  "antagonist_issues": ["问题1（如有）"],
  "protagonist_adjustments": {{
    "want": "调整后的want（若无需调整则与原文相同）",
    "fear": "调整后的fear",
    "contradiction": "调整后的contradiction"
  }},
  "antagonist_adjustments": {{
    "want": "调整后的want",
    "fear": "调整后的fear",
    "contradiction": "调整后的contradiction"
  }},
  "alignment_notes": "对齐说明（简短）"
}}"""

USER_VOLUME_HINT_PROMPT = """从以下用户提供的小说描述中，提取每一卷的核心信息。

【用户描述】
{user_input}

【总卷数】{total_volumes}

对每一卷，提取用户明确提到的内容（没有提到的卷留空即可）。
输出JSON：
{{
  "volume_hints": [
    {{
      "volume_no": 1,
      "title_hint": "用户给出的卷名（若有）",
      "core_goal": "用户描述的本卷核心目标/主线",
      "key_plots": ["用户明确提到的关键情节1", "情节2"],
      "climax_hint": "用户描述的高潮/转折点",
      "ending_hint": "用户描述的卷末悬念/结尾",
      "tone_hint": "用户描述的本卷基调（若有）"
    }}
  ]
}}"""

USER_CHARACTER_EXTRACT_PROMPT = """从以下用户提供的小说描述中，提取所有明确命名的角色。

【用户描述】
{user_input}

【已存储的角色】
{existing_chars}

只提取【用户描述】中明确出现名字的角色。对每个【尚未存储】的角色，提供基本信息。
输出JSON：
{{
  "missing_characters": [
    {{
      "name": "角色名",
      "char_id": "角色名拼音或英文id（小写下划线）",
      "role": "角色定位",
      "personality": ["性格特征1"],
      "backstory": "背景简介",
      "status": "alive",
      "want": "核心目标",
      "fear": "内心恐惧",
      "contradiction": "核心矛盾张力",
      "relationship_to_protagonist": "与主角关系"
    }}
  ]
}}"""

AUTHOR_INTENT_PROMPT = """基于以下小说信息，生成作者长期意图声明：

【故事标题】{title}
【核心主题】{theme}
【主角信息】{protagonist}
【故事结局方向】{ending}
【用户原始需求】{user_input}

请生成一段简洁的作者意图声明（100字以内），描述：
1. 故事整体基调
2. 主角成长方向
3. 核心矛盾
4. 结局方向

直接输出意图声明文本，不要JSON："""

CURRENT_FOCUS_PROMPT = """基于当前小说进展，生成当前创作聚焦方向：

【故事进度】第{volume_no}/{total_volumes}卷完成（剩余{remaining_volumes}卷）
【最近剧情摘要】{recent_summary}
【主角当前状态】{protagonist_state}
【未完成伏笔】{pending_foreshadows}
【全书大纲当前阶段】{current_act}

【进度压力提示】
已写完全书的 {progress_pct}%。根据剩余卷数调整节奏：
- 若剩余卷数充裕（> total_volumes * 0.3）：正常推进
- 若剩余适中（total_volumes * 0.15 ~ total_volumes * 0.3）：加快主线推进速度
- 若剩余较少（< total_volumes * 0.15）：必须加速！聚焦主线，压缩支线，开始回收伏笔
- 若剩余仅1卷：本卷之后故事必须完结！聚焦大结局

请生成当前1-2卷的创作聚焦方向（100字以内），说明：
1. 当前阶段重点（考虑进度位置）
2. 需要推进的情节（如时间紧迫则强调核心主线）
3. 需要注意的约束

"""

# ════════════════════════════════════════════════════════════
#  CHAPTER REWRITE (expand / compress / change tone)
# ════════════════════════════════════════════════════════════

CHAPTER_EXPAND_PROMPT = """你是小说扩写专家。请将以下章节扩写到{target_chars}字左右（当前{current_chars}字）。

扩写策略（按优先级）：
1. 环境/氛围描写更丰富——增加感官细节（视觉、听觉、嗅觉、触觉）
2. 角色内心独白更深入——挖掘角色的微妙情绪和矛盾心理
3. 对话之间增加动作/微表情描写——"他说" → "他端起茶杯，目光有些游离，低声说"
4. 战斗/动作场面更详细——增加速度、力量、空间感的描写
5. 过渡场景增加细节——角色走动时看到的风景、听到的声音

【原文】
{chapter_text}

【约束】
- 不改变核心剧情和主线走向
- 不添加新角色或新事件
- 保持原有的风格基调和节奏
- 保持原有的角色性格设定
- 直输出扩写后的正文，不要任何额外说明"""

CHAPTER_COMPRESS_PROMPT = """你是小说精简专家。请将以下章节压缩到{target_chars}字左右（当前{current_chars}字）。

压缩策略（按优先级）：
1. 合并冗余的修饰词和描写——去除重复的形容词
2. 精简环境描写——保留必要的氛围渲染，去掉过度堆砌的细节
3. 合并角色内心独白——只保留对剧情推进最关键的心理活动
4. 精简对话中的口头禅和重复表达
5. 快速推进过渡场景

【原文】
{chapter_text}

【约束】
- 保持核心剧情完整（所有关键情节不能删）
- 保持角色性格和人物真实感
- 保持故事的逻辑连贯性
- 直输出压缩后的正文，不要任何额外说明"""

OPENING_CHAPTER_DIRECTIVES = {
    1: """━━ 第一章 · 开篇强化 ━━
1. 前200字内必须出现：具体的人 + 具体的事 + 具体的冲突/矛盾。禁止任何背景介绍、天气描写、时间说明。
2. 主角第一次出场必须有「有性格的动作」——主动做某事、说某句话、做出某个决定。不能是被动醒来/环顾四周/接收信息。
3. 结尾必须有悬疑钩子——悬念、反转、未完成的对话、突如其来的变故——让读者必须点下一章。
4. 对话要有真实感：口癖、停顿、重复、骂人、语气词。禁止「他说」「她认为」等叙述标签替代对话本身。
5. 不要解释设定。设定通过行动和对话自然流露。""",
    2: """━━ 第二章 · 冲突升级强化 ━━
1. 第一章的矛盾不能解决，要扩大——引入新威胁、暴露新问题、加深困境。
2. 引入第二个有辨识度的角色，TA的出场要有记忆点（独特的动作、对话、态度）。
3. 核心设定/金手指要通过行动展示，禁止旁白解释。
4. 章末钩子比第一章更强——不只悬念，还要让读者感到「事情没那么简单」。""",
    3: """━━ 第三章 · 首次小高潮强化 ━━
1. 三章内必须有第一次「爽点」——主角小胜、打脸、关键信息解锁、压抑后的释放——选一个。
2. 收束前3章积累的小悬念，同时抛出更大的主线悬念。
3. 让读者觉得「后面还有更大的」——制造期待感。
4. 情绪节奏：从低到高，最后推到章节最高点收尾。""",
}

ENDING_VOLUME_DIRECTIVE = """━━ 结尾卷收尾强化（第{volume_no}卷/共{total_volumes}卷） ━━
1. 本章是完结卷的一部分，每个场景都要推动剧情走向终点，禁止灌水拖节奏。
2. 每章必须有明确的「收束感」——解决一个矛盾、揭示一个真相、给一段关系一个交代。
3. 角色弧光的终点要在本章体现——角色的成长/变化要在行动和选择中呈现。
4. 核心伏笔从本章起逐步回收，每章至少处理一条主线伏笔。
5. 节奏从「推进」转向「收束」，但到最终章前仍要保持剧情张力，不能提前泄气。"""

ENDING_CHAPTER_DIRECTIVE = """━━ 最终章 · 情感高潮强化 ━━
1. 情绪是本章的第一优先级，情节服务于情绪——每个场景都要问：读者此刻应该感受到什么？
2. 情感释放技法：内心独白（碎片化、口语化）+ 身体感知（颤抖、哽咽、呼吸急促）+ 环境烘托，三者至少用两种。
3. 给核心角色一个「告别时刻」——用行动或对话完成的告别，比旁白叙述有力十倍。
4. 结局必须有「情感余韵」——最后一段话要让读者合上书本后还在回味。可以是意象、留白、轮回式的呼应开头。
5. 核心伏笔全部回收。可以有开放式结局，但不能有未交代的核心悬念。
6. 文字密度要高于普通章节——每一句话都有分量，删掉所有可有可无的描述。"""

CHAPTER_TONE_PROMPT = """你是小说风格调整专家。请将以下章节调整为【{target_tone}】的风格基调。

当前风格参考信息：
{style_context}

调整策略：
- 如果调整为"压抑"：增加环境阴郁感、角色沉重感、对话减少、用词更冷
- 如果调整为"热血"：增加情绪张力、对话激昂、行动描写更有力
- 如果调整为"轻松"：增加幽默感、角色互动更活泼、对话节奏更快
- 如果调整为"温馨"：增加细腻的情感描写、温馨的环境烘托、角色间温暖的互动
- 如果调整为"紧张"：增加紧迫感、短句更多、氛围描写更紧绷
- 如果调整为"悲伤"：增加失落感、回忆穿插、情绪描写更细腻

【原文】
{chapter_text}

【约束】
- 不改变核心剧情和发生的事件
- 不改变角色性格（只改变表达方式）
- 不添加新角色或新事件
- 直输出改写后的正文，不要任何额外说明"""
