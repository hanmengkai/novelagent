"""
db/json_session.py — JSON-backed SQL-compatible session

提供与 SQLAlchemy 兼容的 get_db() 上下文管理器，
所有数据实际读写自 JSON 文件（通过 file_store.py）。
"""
import json
import re
from contextlib import contextmanager
from typing import Any, Optional
from loguru import logger
from . import file_store as fs


# ── Mock result objects ──────────────────────────────────

class _ScalarResult:
    """模拟 SQLAlchemy scalar()，用于 SELECT COUNT / MAX / MIN 等"""
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def mappings(self):
        return self

    def all(self):
        return [self._value] if self._value is not None else []

    def first(self):
        return self._value


class _MappingResult:
    """模拟 SQLAlchemy RowMapping"""
    def __init__(self, data: dict):
        self._data = data or {}

    def __getitem__(self, key):
        return self._data.get(key)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def __bool__(self):
        return bool(self._data)


class _MappingsList:
    """模拟 SQLAlchemy mappings().all() / .first()"""
    def __init__(self, rows: list[dict]):
        self._rows = [_MappingResult(r) for r in rows]

    def all(self) -> list[_MappingResult]:
        return self._rows

    def first(self) -> Optional[_MappingResult]:
        return self._rows[0] if self._rows else None

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, idx):
        return self._rows[idx]

    def scalar(self):
        if self._rows:
            vals = list(self._rows[0]._data.values())
            return vals[0] if vals else None
        return None


class _CursorResult:
    """模拟 SQLAlchemy execute() 的结果"""
    def __init__(self, data=None, rowcount=0):
        self._data = data  # list[dict] or dict or None
        self._rowcount = rowcount
        self._scalar_val = None

        # If single dict, extract scalar
        if isinstance(data, dict):
            vals = list(data.values())
            self._scalar_val = vals[0] if len(vals) == 1 else None

    @property
    def rowcount(self):
        return self._rowcount

    def scalar(self):
        return self._scalar_val

    def mappings(self):
        if isinstance(self._data, list):
            return _MappingsList(self._data)
        elif isinstance(self._data, dict):
            return _MappingsList([self._data])
        return _MappingsList([])

    def fetchall(self):
        if isinstance(self._data, list):
            return [(tuple(d.values()) if isinstance(d, dict) else d) for d in self._data]
        return []

    def first(self):
        if isinstance(self._data, list) and self._data:
            d = self._data[0]
            return tuple(d.values()) if isinstance(d, dict) else d
        return None


# ── SQL parser ────────────────────────────────────────────

def _parse_sql(sql: str, params: dict) -> tuple[str, dict]:
    """
    简单解析 SQL，提取操作类型和表名。
    只支持本项目用到的 SQL 模式。
    """
    sql_stripped = ' '.join(sql.split()).strip()
    sql_upper = sql_stripped.upper()

    # INSERT ... ON DUPLICATE KEY UPDATE → upsert
    # DELETE → delete
    # UPDATE → update
    # SELECT → select

    if sql_upper.startswith("SELECT"):
        return ("SELECT", sql_stripped, params)
    elif sql_upper.startswith("INSERT"):
        return ("INSERT", sql_stripped, params)
    elif sql_upper.startswith("UPDATE"):
        return ("UPDATE", sql_stripped, params)
    elif sql_upper.startswith("DELETE"):
        return ("DELETE", sql_stripped, params)
    return ("UNKNOWN", sql_stripped, params)


# ── Mock DB session ──────────────────────────────────────

class _JsonSession:
    """
    模拟 SQLAlchemy Session，所有操作转发到 JSON 存储。
    只支持本项目实际用到的 SQL 模式。
    """

    def execute(self, sql, params=None):
        # 处理 text() 包裹
        sql_str = str(sql)
        if isinstance(params, dict):
            # 替换 :param 为 %(param)s 兼容格式？不需要，我们自己处理
            pass
        op, parsed_sql, parsed_params = _parse_sql(sql_str, params or {})
        # 自动替换 :param 为实际值
        resolved_params = self._resolve_params(parsed_sql, parsed_params)

        try:
            return self._route(op, parsed_sql, resolved_params)
        except Exception as e:
            logger.error(f"[JsonDB] SQL 执行失败: {parsed_sql[:100]} | params={parsed_params} | error={e}")
            return _CursorResult(data=[], rowcount=0)

    def _resolve_params(self, sql: str, params: dict) -> dict:
        """将 :param 替换为实际值"""
        resolved = {}
        for key, val in params.items():
            resolved[key] = val
        return resolved

    def _extract_table(self, sql: str) -> str:
        """从 SQL 中提取表名"""
        sql_upper = sql.upper()
        for kw in ["FROM", "INTO", "UPDATE", "TABLE"]:
            idx = sql_upper.find(kw)
            if idx >= 0:
                after = sql[idx + len(kw):].strip().split()[0]
                return after.strip('"`\'')
        return "unknown"

    def _get_novel_id(self, params: dict) -> Optional[str]:
        return params.get("nid") or params.get("novel_id")

    def _route(self, op: str, sql: str, params: dict) -> _CursorResult:
        novel_id = self._get_novel_id(params)

        if op == "SELECT":
            return self._handle_select(sql, params, novel_id)
        elif op == "INSERT":
            return self._handle_insert(sql, params, novel_id)
        elif op == "UPDATE":
            return self._handle_update(sql, params, novel_id)
        elif op == "DELETE":
            return self._handle_delete(sql, params, novel_id)
        else:
            return _CursorResult(data=[], rowcount=0)

    def _get_table_data(self, novel_id: Optional[str], table: str) -> Any:
        """从 JSON 存储中读取表数据"""
        if table == "novels":
            return fs.load_global("novels_index", default=[])
        if novel_id:
            section = _TABLE_TO_SECTION.get(table, table)
            return fs.load_json(novel_id, section, default={})
        return {}

    def _save_table_data(self, novel_id: Optional[str], table: str, data: Any):
        section = _TABLE_TO_SECTION.get(table, table)
        if table == "novels":
            fs.save_global("novels_index", data)
        elif novel_id:
            fs.save_json(novel_id, section, data)

    def _handle_select(self, sql: str, params: dict, novel_id: Optional[str]) -> _CursorResult:
        table = self._extract_table(sql)
        data = self._get_table_data(novel_id, table)

        if isinstance(data, dict):
            # Dict-based table: chapters, foreshadows, etc.
            items = list(data.values())
        elif isinstance(data, list):
            items = data
        else:
            items = []

        # Apply WHERE filtering
        items = self._apply_where(sql, items, params, novel_id)

        # Apply ORDER BY
        items = self._apply_order_by(sql, items)

        # Apply LIMIT
        items = self._apply_limit(sql, items)

        return _CursorResult(data=items, rowcount=len(items))

    def _apply_where(self, sql: str, items: list, params: dict, novel_id: Optional[str] = None) -> list:
        """简单 WHERE 过滤"""
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+ORDER BY|\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
        if not where_match:
            return items

        where_clause = where_match.group(1).strip()
        filtered = []

        # Special case: IN clause
        in_match = re.search(r'(\w+)\s+IN\s*\(([^)]+)\)', where_clause)
        in_field = None
        in_values = []
        if in_match:
            in_field = in_match.group(1)
            raw_values = in_match.group(2)
            # Replace :param references
            for key, val in params.items():
                if f":{key}" in raw_values or f"%({key})s" in raw_values:
                    if isinstance(val, (list, tuple)):
                        in_values = val
                    else:
                        in_values = [val]

        for item in items:
            match = True
            if isinstance(item, dict):
                # Simple equality checks
                for key, val in params.items():
                    if key in ('nid', 'novel_id', 'lim', 'limit', 's', 'start', 'end', 
                               'prev', 'vno', 'volume_no', 'cn', 'chapter_no', 't', 'title',
                               'bc', 'drs', 'dre', 'state', 'fid', 'fshadow_id',
                               'n', 'last_n', 'cid', 'char_id', 'name', 'desc',
                               'wn', 'v', 'k', 'wt', 'tv', 'rc', 'imp', 'extra',
                               'id', 'status', 'nid_1', 'nid_2', 'ft', 'text', 'kw',
                               'kw0', 'kw1', 'sc', 'qs', 'issues', 'repair',
                               'eng', 'ten', 'dr', 'raw', 'an', 'pt', 'ct', 'em',
                               's', 'buried', 'resolved', 'cn_start', 'cn_end'):
                        continue
                    # Try matching field
                    field_val = item.get(key)
                    if field_val is not None and val is not None:
                        if str(field_val) != str(val):
                            match = False
                            break

                # Check novel_id
                if novel_id and item.get("novel_id") and item["novel_id"] != novel_id:
                    match = False

                # Check :nid parameter
                if params.get("nid") and item.get("novel_id") and item["novel_id"] != params["nid"]:
                    match = False

                # Check volume_no
                if params.get("vn") and item.get("volume_no") and item["volume_no"] != params["vn"]:
                    match = False

                # Check chapter_no
                if params.get("cn") and item.get("chapter_no") and item["chapter_no"] != params["cn"]:
                    match = False

                # BETWEEN clause
                between = re.search(r'(\w+)\s+BETWEEN\s+:(\w+)\s+AND\s+:(\w+)', where_clause, re.IGNORECASE)
                if between:
                    field, start_p, end_p = between.group(1), between.group(2), between.group(3)
                    start_val = params.get(start_p)
                    end_val = params.get(end_p)
                    if start_val is not None and end_val is not None:
                        field_val = item.get(field)
                        if field_val is not None:
                            try:
                                if not (int(start_val) <= int(field_val) <= int(end_val)):
                                    match = False
                            except (ValueError, TypeError):
                                pass

                # >= clause
                ge = re.search(r'(\w+)\s*>=\s*:(\w+)', where_clause, re.IGNORECASE)
                if ge:
                    field, param = ge.group(1), ge.group(2)
                    val = params.get(param)
                    if val is not None:
                        field_val = item.get(field)
                        if field_val is not None:
                            try:
                                if not (int(field_val) >= int(val)):
                                    match = False
                            except (ValueError, TypeError):
                                pass

                # status filter
                if params.get("s") and item.get("status") and item["status"] != params["s"]:
                    match = False

                # title filter
                if params.get("t") and item.get("title"):
                    if item["title"] != params["t"]:
                        match = False

                # state filter
                if params.get("state") and item.get("state") and item["state"] != params["state"]:
                    match = False

                # LIKE clause
                like = re.search(r'(\w+)\s+LIKE\s+:(\w+)', where_clause, re.IGNORECASE)
                if like:
                    field, param = like.group(1), like.group(2)
                    val = params.get(param)
                    if val and '%' in str(val):
                        pattern = str(val).replace('%', '')
                        field_val = str(item.get(field, ''))
                        if pattern not in field_val:
                            match = False

                # != clause
                ne = re.findall(r'(\w+)\s*!=\s*:(\w+)', where_clause)
                for field, param in ne:
                    val = params.get(param)
                    if val is not None:
                        if item.get(field) == val:
                            match = False

                # AND chaining additional conditions
                # <= clause
                le = re.search(r'(\w+)\s*<=\s*:(\w+)', where_clause, re.IGNORECASE)
                if le:
                    field, param = le.group(1), le.group(2)
                    val = params.get(param)
                    if val is not None:
                        field_val = item.get(field)
                        if field_val is not None:
                            try:
                                if not (int(field_val) <= int(val)):
                                    match = False
                            except (ValueError, TypeError):
                                pass

            if match:
                filtered.append(item)

        return filtered

    def _apply_order_by(self, sql: str, items: list) -> list:
        order = re.search(r'ORDER BY\s+(.+?)(?:\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
        if not order:
            return items

        order_clause = order.group(1).strip()
        desc = 'DESC' in order_clause.upper()

        # Extract field name
        field_match = re.search(r'(\w+)', order_clause)
        if not field_match:
            return items

        field = field_match.group(1)
        try:
            return sorted(items, key=lambda x: (x.get(field) if isinstance(x, dict) else 0) or 0, reverse=desc)
        except TypeError:
            return items

    def _apply_limit(self, sql: str, items: list) -> list:
        limit = re.search(r'LIMIT\s+:?(\w+)', sql, re.IGNORECASE)
        if not limit:
            return items
        try:
            n = int(limit.group(1))
            return items[:n]
        except ValueError:
            return items

    def _handle_insert(self, sql: str, params: dict, novel_id: Optional[str]) -> _CursorResult:
        table = self._extract_table(sql)
        data = self._get_table_data(novel_id, table)

        if isinstance(data, list):
            # Append for list-based tables (llm_task_log, chapter_facts)
            data.append(params)
        elif isinstance(data, dict):
            # Upsert for dict-based tables
            # Extract key field
            key_field = None
            if 'fshadow_id' in params:
                key_field = 'fshadow_id'
            elif 'chapter_no' in params:
                key_field = str(params['chapter_no'])
            elif 'char_id' in params:
                key_field = params['char_id']
            elif 'mem_key' in params:
                key_field = params['mem_key']
            elif 'volume_no' in params:
                key_field = str(params['volume_no'])
            elif 'mem_value' in params:
                key_field = params.get('k') or params.get('mem_key', list(params.keys())[0])

            if key_field:
                entry_key = str(key_field)
                data[entry_key] = params

        self._save_table_data(novel_id, table, data)
        return _CursorResult(rowcount=1)

    def _handle_update(self, sql: str, params: dict, novel_id: Optional[str]) -> _CursorResult:
        """简单 UPDATE，只支持 SET field=:param WHERE ..."""
        table = self._extract_table(sql)
        data = self._get_table_data(novel_id, table)

        # Extract SET fields
        set_match = re.search(r'SET\s+(.+?)(?:\s+WHERE|\s*$)', sql, re.IGNORECASE | re.DOTALL)
        if not set_match:
            return _CursorResult(rowcount=0)

        set_clause = set_match.group(1)
        set_pairs = re.findall(r'(\w+)\s*=\s*(?:VALUES\((\w+)\)|:(\w+))', set_clause)

        # Find target items via WHERE
        targets = self._apply_where(sql, 
            list(data.values()) if isinstance(data, dict) else data, params, novel_id)

        rowcount = 0
        for target in targets:
            if isinstance(target, dict):
                for field, val_field, param_field in set_pairs:
                    field_name = val_field or field
                    if field_name in params:
                        target[field] = params[field_name]
                rowcount += 1

        self._save_table_data(novel_id, table, data)
        return _CursorResult(rowcount=rowcount)

    def _handle_delete(self, sql: str, params: dict, novel_id: Optional[str]) -> _CursorResult:
        table = self._extract_table(sql)
        data = self._get_table_data(novel_id, table)

        # For volume deletion, delete by volume_no
        if params.get("vno") is not None and isinstance(data, dict):
            vno = params["vno"]
            keys_to_delete = [k for k, v in data.items() if isinstance(v, dict) and v.get("volume_no") == vno]
            for k in keys_to_delete:
                del data[k]
            rowcount = len(keys_to_delete)
        elif params.get("nid") and params.get("nid_2"):
            # Multi-table delete
            return _CursorResult(rowcount=0)
        else:
            # Simple delete by filtering
            targets = self._apply_where(sql, 
                list(data.values()) if isinstance(data, dict) else data, params, novel_id)
            target_ids = set()
            for t in targets:
                if isinstance(t, dict):
                    target_ids.add(id(t))

            if isinstance(data, dict):
                data = {k: v for k, v in data.items() if id(v) not in target_ids}
            else:
                data = [x for x in data if id(x) not in target_ids]
            rowcount = len(target_ids)

        self._save_table_data(novel_id, table, data)
        return _CursorResult(rowcount=rowcount)

    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


# ── 表名 → JSON section 映射 ────────────────────────────

_TABLE_TO_SECTION = {
    "novels": "novels_index",  # 全局索引
    "world_memory": "core",    # 存储在 core.json 中
    "characters": "characters",
    "foreshadowing": "foreshadows",
    "world_rules": "world_rules",
    "chapters": "chapters",
    "chapter_facts": "chapter_facts",
    "chapter_summaries": "chapter_summaries",
    "reader_metrics": "reader_metrics",
    "llm_task_log": "llm_log",
    "volumes": "core",         # 存储在 core.json 中
}


# ── 公开接口 ──────────────────────────────────────────────

@contextmanager
def get_db():
    """提供与 SQLAlchemy 兼容的数据库会话上下文管理器"""
    session = _JsonSession()
    try:
        yield session
    finally:
        session.close()


def ping() -> bool:
    """检查存储是否可用"""
    try:
        fs.list_novel_ids()
        return True
    except Exception:
        return False
