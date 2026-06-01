#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专利数据更新脚本（动态版）

功能：
- 动态计算哪些申请号需要更新
- 基于上次更新的时间和设定的更新频率
- 实时生成更新列表
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(timezone.utc)


def normalize_app_no(app_no: str) -> str:
    """统一申请号格式，便于单号查询。"""
    return str(app_no).upper().replace('CN', '').replace('.', '') if app_no else ''

def load_focus_strategy() -> Dict:
    """加载关注策略配置"""
    from settings import DATA_DIR
    strategy_file = str(DATA_DIR / 'focus_strategy.json')
    if not os.path.exists(strategy_file):
        print(f"❌ 找不到关注策略文件: {strategy_file}")
        sys.exit(1)

    with open(strategy_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_detection_log() -> Dict:
    """加载采集日志（从 PatentsDB 读取，确保与 upsert_record 写入的最新状态一致）"""
    from db_manager import PatentsDB
    from settings import PATENTS_DB_FILE
    db = PatentsDB(PATENTS_DB_FILE)
    return {'records': db.get_all_records()}

def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    解析 ISO 8601 时间戳，统一返回 UTC aware datetime。

    Python < 3.11 的 fromisoformat 不识别末尾 'Z'，手动替换为 '+00:00'。
    无时区信息的时间戳一律视作 UTC（DB 写入端约定）。
    """
    try:
        if not timestamp_str:
            return None
        ts = str(timestamp_str).strip()
        # 兼容 Python 3.9/3.10：'Z' → '+00:00'
        if ts.upper().endswith('Z'):
            ts = ts[:-1] + '+00:00'
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception as e:
        print(f"❌ 无法解析时间戳 {timestamp_str!r}: {e}")
        return None

def calculate_needs_update(last_update_time: datetime, frequency_days: int) -> tuple:
    """
    计算是否需要更新
    逻辑：上次更新时间 + 周期 <= 现在时间 ?

    返回: (是否需要更新, 距离下次更新还有几天, 下次更新应该在何时)
    """
    if last_update_time is None:
        return True, 0, None

    now = utc_now()

    # 计算下次应该更新的时间点
    next_update_time = last_update_time + timedelta(days=frequency_days)

    # 判断：现在是否已经到达或超过了下次更新时间
    needs_update = now >= next_update_time

    if needs_update:
        # 如果需要更新，计算已经超期多少天
        days_overdue = (now - next_update_time).days
        days_until = 0
    else:
        # 如果不需要更新，计算还要多少天才到下次更新时间
        days_until = (next_update_time - now).days
        days_overdue = 0

    return needs_update, days_until, next_update_time

def _build_update_info(record: dict, freq_days: int, needs_update: bool) -> dict:
    """将 DB 记录转换为 analyze_updates 所需的更新状态字典。"""
    timestamp_str = record.get('timestamp')
    last_update_time = parse_timestamp(timestamp_str)
    _, days_until, next_update_time = calculate_needs_update(last_update_time, freq_days)
    days_since = (utc_now() - last_update_time).days if last_update_time else None
    return {
        'app_no': record.get('application_no'),
        'status': record.get('anjianywzt'),
        'last_update': timestamp_str,
        'last_update_time': last_update_time,
        'next_update_time': next_update_time,
        'days_since': days_since,
        'days_until': days_until,
        'needs_update': needs_update,
    }


def analyze_updates(show_all: bool = False):
    """
    分析所有申请号，计算哪些需要更新。

    一次查询拉取 (application_no, anjianywzt, timestamp) 快照，
    在 Python 侧按 focus_strategy 分组，避免 N 个状态 = N 次全表扫描。

    Args:
        show_all: 保留参数，供调用方兼容（当前实现未过滤，始终返回完整结果）
    """
    from db_manager import PatentsDB
    from settings import PATENTS_DB_FILE

    focus_strategy = load_focus_strategy()
    status_breakdown = focus_strategy.get('status_breakdown', {})
    db = PatentsDB(PATENTS_DB_FILE)

    # 构建 status → freq_days 映射，供后续 O(1) 查找
    status_to_freq: dict = {
        status: info['frequency_days']
        for status, info in status_breakdown.items()
    }

    results = {}
    # 初始化所有频率分组（确保输出键顺序与策略一致）
    for status, info in status_breakdown.items():
        freq_days = info['frequency_days']
        if freq_days not in results:
            results[freq_days] = {
                'status_list': [],
                'needs_update': [],
                'no_update_needed': [],
                'status_names': [],
            }
        results[freq_days]['status_names'].append(status)

    # 一次查询，Python 侧分组
    now = utc_now()
    for snap in db.get_status_timestamp_snapshot():
        status = snap.get('anjianywzt')
        freq_days = status_to_freq.get(status)
        if freq_days is None:
            # 不在 focus_strategy 中的状态（如已失效），跳过
            continue

        last_update_time = parse_timestamp(snap.get('timestamp'))
        needs_update, days_until, next_update_time = calculate_needs_update(
            last_update_time, freq_days
        )
        days_since = (now - last_update_time).days if last_update_time else None
        update_info = {
            'app_no': snap['application_no'],
            'status': status,
            'last_update': snap.get('timestamp'),
            'last_update_time': last_update_time,
            'next_update_time': next_update_time,
            'days_since': days_since,
            'days_until': days_until,
            'needs_update': needs_update,
        }
        bucket = 'needs_update' if needs_update else 'no_update_needed'
        results[freq_days][bucket].append(update_info)

    return results

def show_statistics():
    """显示关注策略统计信息"""
    focus_strategy = load_focus_strategy()
    status_breakdown = focus_strategy.get('status_breakdown', {})

    print("\n" + "=" * 100)
    print("📊 关注策略统计 - 按更新频率分类")
    print("=" * 100)

    # 按频率分组显示
    frequency_groups = {}
    for status, info in status_breakdown.items():
        freq_days = info['frequency_days']
        freq_name = info['frequency_name']
        if freq_days not in frequency_groups:
            frequency_groups[freq_days] = {'name': freq_name, 'statuses': []}
        frequency_groups[freq_days]['statuses'].append((status, info['count']))

    # 按频率天数排序显示
    for freq_days in sorted(frequency_groups.keys()):
        group = frequency_groups[freq_days]
        freq_name = group['name']
        statuses = group['statuses']
        total_for_freq = sum(count for _, count in statuses)
        percentage = round(total_for_freq / len(load_detection_log()['records']) * 100, 1)

        print(f"\n【🕒 {freq_name}（{freq_days}天）】")
        print(f"  申请号数: {total_for_freq} 件 ({percentage}%)")
        print(f"  包含状态:")
        for status, count in statuses:
            print(f"    - {status}: {count} 件")

    print(f"\n【总体数据】")
    print(f"  总申请号数: {focus_strategy['total_count']} 件")
    print(f"  占总数比例: {focus_strategy['total_percentage']}%")
    print(f"  优先级: {focus_strategy['priority']}")

def sort_by_update_time(items: List[Dict], ascending: bool = True) -> List[Dict]:
    """按下次更新时间排序申请号列表。"""
    return sorted(
        items,
        key=lambda x: x['next_update_time'] or (
            datetime.min.replace(tzinfo=timezone.utc) if ascending
            else datetime.max.replace(tzinfo=timezone.utc)
        ),
        reverse=not ascending
    )


def ensure_previous_status(detection_log: Dict) -> Dict:
    """确保所有记录都有 previous_status 字段。
    初始化为 None（而非当前状态），表示"从未做过基准快照"，
    这样首次运行 prepare→采集→diff 时不会产生误报的"无变化"结论。
    """
    records = detection_log.get('records', [])
    for record in records:
        if 'previous_status' not in record:
            record['previous_status'] = None
    return detection_log


def save_detection_log_with_previous_status(detection_log: Dict):
    """保存包含 previous_status 字段的 detection_log（JSONL 格式，原子重写）。"""
    from settings import DETECTION_LOG_JSONL_FILE
    log_file = str(DETECTION_LOG_JSONL_FILE)
    tmp = log_file + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for record in detection_log.get('records', []):
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    os.replace(tmp, log_file)


def get_status_change_type(prev_status: Optional[str], curr_status: Optional[str]) -> Optional[str]:
    """判断状态变化类型。"""
    if not prev_status or not curr_status:
        return None
    if prev_status == curr_status:
        return None

    # 关键变化
    if curr_status == '驳回等复审请求':
        return 'REJECTION'  # 进入驳回
    if curr_status in ['专利权维持', '等年登印费', '准备颁证公告']:
        return 'GRANTED'  # 进入授权
    if curr_status in ['驳回失效', '逾期视撤失效', '撤回专利申请']:
        return 'INVALID'  # 失效/撤回
    if '复审' in curr_status:
        return 'REEXAMINATION'  # 进入复审

    return 'OTHER'  # 其他变化


def prepare_for_update():
    """在执行采集前，将当前 anjianywzt 快照到 DB 的 previous_status 字段，用于采集后对比分析。"""
    from db_manager import PatentsDB
    from settings import PATENTS_DB_FILE
    db = PatentsDB(PATENTS_DB_FILE)
    records = db.get_all_records()

    count = 0
    for record in records:
        current_status = record.get('anjianywzt')
        if current_status:
            db.update_fields(record['application_no'], {'previous_status': current_status})
            count += 1

    print("\n" + "=" * 100)
    print("🔄 采集前准备 - 状态快照已保存")
    print("=" * 100)
    print(f"\n已保存 {count} 条记录的当前状态到 previous_status")
    print(f"\n下一步：")
    print(f"  1. 执行采集更新:")
    print(f"     USE_MITM_PROXY=true python main_automation.py --update-list data/update_list_dynamic.txt")
    print(f"  2. 采集完成后，运行:")
    print(f"     python update_by_strategy.py diff      # 查看状态变化")
    print(f"     python update_by_strategy.py report    # 查看统计数据")
    print("\n" + "=" * 100)


def show_status_changes():
    """显示自上次生成清单后，所有申请的状态变化。"""
    detection_log = load_detection_log()
    records = detection_log.get('records', [])

    # 确保有 previous_status 字段
    ensure_previous_status(detection_log)

    print("\n" + "=" * 100)
    print("📊 申请号状态变化分析")
    print("=" * 100)
    print(f"\n当前时间: {utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    # 统计各类变化
    changes_by_type = {
        'REJECTION': [],
        'GRANTED': [],
        'INVALID': [],
        'REEXAMINATION': [],
        'OTHER': [],
        'NO_CHANGE': [],
        # previous_status=None 表示从未执行过 prepare，无法做对比
        'NO_BASELINE': [],
    }

    for record in records:
        app_no = record.get('application_no')
        curr_status = record.get('anjianywzt')
        prev_status = record.get('previous_status')

        change_info = {
            'app_no': app_no,
            'prev_status': prev_status if prev_status is not None else '（无基准）',
            'curr_status': curr_status or 'N/A',
            'timestamp': record.get('timestamp')
        }

        # previous_status=None 意味着此条记录导入后尚未执行 prepare，不计入变化统计
        if prev_status is None:
            changes_by_type['NO_BASELINE'].append(change_info)
            continue

        change_type = get_status_change_type(prev_status, curr_status)
        if change_type:
            changes_by_type[change_type].append(change_info)
        else:
            changes_by_type['NO_CHANGE'].append(change_info)

    # 显示结果
    type_names = {
        'REJECTION': ('🚨 进入驳回（驳回等复审请求）', 'REJECTION'),
        'GRANTED': ('✅ 进入授权', 'GRANTED'),
        'INVALID': ('❌ 失效/撤回', 'INVALID'),
        'REEXAMINATION': ('🔄 进入复审程序', 'REEXAMINATION'),
        'OTHER': ('⚠️  其他状态变化', 'OTHER'),
        'NO_CHANGE': ('➡️  状态无变化', 'NO_CHANGE'),
        'NO_BASELINE': ('🆕 尚无基准快照（需先运行 prepare）', 'NO_BASELINE'),
    }

    total_changed = 0
    for change_type, (display_name, _) in type_names.items():
        items = changes_by_type[change_type]
        if not items:
            continue

        print(f"\n{display_name}: {len(items)} 件")

        if change_type in ['REJECTION', 'GRANTED', 'INVALID']:
            total_changed += len(items)
            # 显示前5个
            for i, info in enumerate(items[:5], 1):
                print(f"  {i}. {info['app_no']:15s} | {info['prev_status'][:10]:10s} → {info['curr_status'][:10]:10s}")
            if len(items) > 5:
                print(f"  ... 还有 {len(items) - 5} 件")

    print("\n" + "=" * 100)
    print(f"🎯 总计发现状态变化: {total_changed} 件")
    if changes_by_type['REJECTION']:
        print(f"   ⚠️  其中进入驳回的: {len(changes_by_type['REJECTION'])} 件 ⭐ 【重点关注】")
    print("=" * 100)


def show_update_status(frequency_days: int = None):
    """
    显示各申请号的更新状态

    Args:
        frequency_days: 如果指定，只显示该频率的申请号
    """
    focus_strategy = load_focus_strategy()
    detection_log = load_detection_log()
    status_breakdown = focus_strategy.get('status_breakdown', {})

    results = analyze_updates()

    print("\n" + "=" * 100)
    print("🔄 申请号更新状态 - 动态计算")
    print("=" * 100)
    print(f"\n当前时间: {utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    # 按频率显示
    for freq_days in sorted(results.keys()):
        if frequency_days is not None and freq_days != frequency_days:
            continue

        result = results[freq_days]
        needs_update = result['needs_update']
        no_update_needed = result['no_update_needed']
        status_names = result['status_names']

        # 找到频率名称
        freq_name = None
        for status in status_names:
            if status in status_breakdown:
                freq_name = status_breakdown[status]['frequency_name']
                break

        print(f"\n【🕒 {freq_name}（{freq_days}天）- {', '.join(status_names)}】")
        print(f"  总数: {len(needs_update) + len(no_update_needed)} 件")
        print(f"  ✅ 需要更新: {len(needs_update)} 件")
        print(f"  ⏳ 暂无需更新: {len(no_update_needed)} 件")

        if needs_update:
            print(f"\n  【需要更新的申请号】")
            sorted_updates = sort_by_update_time(needs_update, ascending=True)
            for i, info in enumerate(sorted_updates[:10], 1):  # 只显示前 10 个
                last_update_str = info['last_update_time'].strftime('%m-%d') if info['last_update_time'] else 'N/A'
                next_update_str = info['next_update_time'].strftime('%m-%d') if info['next_update_time'] else 'N/A'
                days_since = info['days_since'] if info['days_since'] is not None else 0
                print(f"    {i:2d}. {info['app_no']:15s} | 上次: {last_update_str} | 应更新: {next_update_str} | ({days_since} 天前更新)")
            if len(needs_update) > 10:
                print(f"    ... 还有 {len(needs_update) - 10} 件")

        # 显示最需要更新的（下次更新时间最早的）
        if needs_update:
            most_urgent = sort_by_update_time(needs_update, ascending=True)[0]
            next_update_str = most_urgent['next_update_time'].strftime('%Y-%m-%d') if most_urgent['next_update_time'] else 'N/A'
            print(f"\n  🚨 最紧急: {most_urgent['app_no']} (应该在 {next_update_str} 前更新)")

        if no_update_needed and len(no_update_needed) > 0:
            print(f"\n  【暂无需更新的申请号（下次应更新时间）】")
            sorted_no_update = sort_by_update_time(no_update_needed, ascending=True)
            for i, info in enumerate(sorted_no_update[:5], 1):  # 只显示前 5 个
                next_update_str = info['next_update_time'].strftime('%m-%d') if info['next_update_time'] else 'N/A'
                days_until = info['days_until'] if info['days_until'] is not None else 0
                print(f"    {i}. {info['app_no']:15s} | 下次更新: {next_update_str} (还有 {days_until} 天)")
            if len(no_update_needed) > 5:
                print(f"    ... 还有 {len(no_update_needed) - 5} 件")

def generate_update_list(frequency_days: int = None) -> List[str]:
    """
    动态生成更新列表

    Args:
        frequency_days: 如果指定，只返回该频率的申请号

    Returns:
        需要更新的申请号列表
    """
    results = analyze_updates()

    update_list = []
    for freq_days in sorted(results.keys()):
        if frequency_days is not None and freq_days != frequency_days:
            continue

        needs_update = results[freq_days]['needs_update']
        for info in needs_update:
            update_list.append(info['app_no'])

    return update_list

def _update_list_path_and_title(frequency_days: int = None) -> tuple:
    """返回动态清单绝对路径（Path）和标题。"""
    from settings import DATA_DIR
    if frequency_days is None:
        return DATA_DIR / 'update_list_dynamic.txt', "所有现在需要检查状态的申请号"
    return (
        DATA_DIR / f'update_list_dynamic_{frequency_days}days.txt',
        f"{frequency_days}天内部规则 - 现在需要检查状态的申请号"
    )

def write_update_list_file(frequency_days: int = None):
    """生成更新列表文件并写入磁盘。"""
    update_list = generate_update_list(frequency_days)

    filepath, title = _update_list_path_and_title(frequency_days)
    filename = str(filepath)

    # 即使清单为空也写入文件，避免后续误用旧清单。
    with open(filename, 'w', encoding='utf-8') as f:
        for app_no in update_list:
            f.write(f"{app_no}\n")

    print("=" * 100)
    print(f"🎯 动态生成状态检查清单 - {title}")
    print("=" * 100)
    print(f"\n申请号数量: {len(update_list)} 件")
    print(f"文件位置: {filename}")

    if not update_list:
        print("\n✅ 当前没有需要检查状态的申请号")
        print("   已写入空清单，避免误用上一次生成的旧数据。")
        print("\n" + "=" * 100)
        return

    print(f"\n【完整更新流程】")
    print(f"  # 第1步：保存采集前的状态快照（重要！用于后续对比）")
    print(f"  python update_by_strategy.py prepare")
    print(f"\n  # 第2步：启动 MITM 代理（终端 1）")
    print(f"  source venv/bin/activate")
    print(f"  python start_mitm_proxy.py")
    print(f"\n  # 第3步：执行采集更新（终端 2）")
    print(f"  source venv/bin/activate")
    print(f"  USE_MITM_PROXY=true python main_automation.py --update-list {filename}")
    print(f"\n  # 第4步：采集完成后查看结果")
    print(f"  python update_by_strategy.py diff      # 查看状态变化 ⭐")
    print(f"  python update_by_strategy.py report    # 查看统计数据")
    print("\n" + "=" * 100)
    print(f"✅ 更新列表已保存")
    print("=" * 100)

def show_detailed_report():
    """生成详细的多维度统计报告。"""
    detection_log = load_detection_log()
    records = detection_log.get('records', [])
    focus_strategy = load_focus_strategy()
    status_breakdown = focus_strategy.get('status_breakdown', {})

    print("\n" + "=" * 100)
    print("📈 详细统计报告 - 多维度分析")
    print("=" * 100)
    print(f"\n当前时间: {utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"总记录数: {len(records)}\n")

    # 1. 申请年份分布（监控范围内）
    print("\n【1️⃣  申请年份分布】（驳回前监控范围）")
    year_distribution = {}
    for record in records:
        status = record.get('anjianywzt')
        if status not in status_breakdown:
            continue
        app_no = record.get('application_no')
        # 提取申请年份（通常是申请号前4位）
        year = app_no[:4] if len(app_no) >= 4 else 'UNKNOWN'
        year_distribution[year] = year_distribution.get(year, 0) + 1

    for year in sorted(year_distribution.keys()):
        count = year_distribution[year]
        bar = '█' * (count // 10) if count >= 10 else '▌'
        print(f"  20{year[2:]}: {count:3d} 件 {bar}")

    # 2. 驳回前各状态分布
    print("\n【2️⃣  驳回前各状态分布】")
    status_distribution = {}
    for record in records:
        status = record.get('anjianywzt')
        if status not in status_breakdown:
            continue
        status_distribution[status] = status_distribution.get(status, 0) + 1

    for status in sorted(status_distribution.keys(),
                          key=lambda s: status_distribution[s],
                          reverse=True):
        count = status_distribution[status]
        freq = status_breakdown[status].get('frequency_days')
        percent = round(count / sum(status_distribution.values()) * 100, 1)
        print(f"  {status:15s}: {count:3d} 件 ({percent:5.1f}%) | {freq}天检查周期")

    # 3. 检查周期超期统计
    print("\n【3️⃣  检查周期超期统计】")
    overdue_stats = {}
    for record in records:
        status = record.get('anjianywzt')
        if status not in status_breakdown:
            continue
        timestamp_str = record.get('timestamp')
        last_update = parse_timestamp(timestamp_str)
        if not last_update:
            continue
        freq_days = status_breakdown[status].get('frequency_days')
        needs_update, _, _ = calculate_needs_update(last_update, freq_days)

        if freq_days not in overdue_stats:
            overdue_stats[freq_days] = {'total': 0, 'overdue': 0}
        overdue_stats[freq_days]['total'] += 1
        if needs_update:
            overdue_stats[freq_days]['overdue'] += 1

    for freq_days in sorted(overdue_stats.keys()):
        stat = overdue_stats[freq_days]
        total = stat['total']
        overdue = stat['overdue']
        percent = round(overdue / total * 100, 1) if total > 0 else 0
        status_list = [s for s, info in status_breakdown.items()
                       if info.get('frequency_days') == freq_days]
        print(f"  {freq_days}天周期: {overdue:3d}/{total:3d} 件超期 ({percent:5.1f}%) | {', '.join(status_list)}")

    # 4. 申请人 TOP10
    print("\n【4️⃣  申请人 TOP10】（驳回前监控范围）")
    applicant_dist = {}
    for record in records:
        status = record.get('anjianywzt')
        if status not in status_breakdown:
            continue
        applicant = record.get('shenqingrxm', 'UNKNOWN')
        applicant_dist[applicant] = applicant_dist.get(applicant, 0) + 1

    top10_applicants = sorted(applicant_dist.items(),
                             key=lambda x: x[1],
                             reverse=True)[:10]
    for i, (applicant, count) in enumerate(top10_applicants, 1):
        print(f"  {i:2d}. {applicant[:20]:20s} | {count:3d} 件")

    print("\n" + "=" * 100)


def validate_focus_strategy():
    """验证 focus_strategy.json 中的计数是否与 detection_log.json 一致"""
    focus_strategy = load_focus_strategy()
    detection_log = load_detection_log()
    status_breakdown = focus_strategy.get('status_breakdown', {})
    records = detection_log.get('records', [])

    print("\n" + "=" * 100)
    print("✅ 数据验证 - focus_strategy.json 计数校验")
    print("=" * 100)

    # 统计实际数据
    actual_counts = {}
    for record in records:
        status = record.get('anjianywzt')
        if status in status_breakdown:
            actual_counts[status] = actual_counts.get(status, 0) + 1

    # 对比
    has_error = False
    print(f"\n当前时间: {utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"detection_log.json 总记录数: {len(records)}")
    print(f"\n状态统计校验结果：\n")

    for status, info in status_breakdown.items():
        expected_count = info.get('count', 0)
        actual_count = actual_counts.get(status, 0)
        match = "✅" if expected_count == actual_count else "❌"

        if expected_count != actual_count:
            has_error = True
            print(f"{match} {status}")
            print(f"   配置中: {expected_count} | 实际数: {actual_count}")
        else:
            print(f"{match} {status}: {actual_count} 件")

    # 显示配置中未出现的其他状态
    other_statuses = set(actual_counts.keys()) - set(status_breakdown.keys())
    if other_statuses:
        print(f"\n⚠️  未被监控的其他状态：")
        for status in sorted(other_statuses):
            print(f"   - {status}: {actual_counts[status]} 件")

    print("\n" + "=" * 100)
    if has_error:
        print("❌ 数据不一致！建议运行 'python update_by_strategy.py stats' 更新统计")
    else:
        print("✅ 所有数据验证通过")
    print("=" * 100)


def check_application(application_no: str):
    """判断单个申请号现在是否需要检查状态。"""
    normalized_app_no = normalize_app_no(application_no)
    if not normalized_app_no:
        print("❌ 申请号不能为空")
        sys.exit(1)

    focus_strategy = load_focus_strategy()
    detection_log = load_detection_log()
    status_breakdown = focus_strategy.get('status_breakdown', {})
    records = detection_log.get('records', [])

    record = None
    for item in records:
        if normalize_app_no(item.get('application_no')) == normalized_app_no:
            record = item

    print("=" * 100)
    print(f"🔎 单个申请号状态检查判断: {normalized_app_no}")
    print("=" * 100)

    if record is None:
        print("\n结论: 未找到该申请号")
        print("原因: detection_log.json 中没有对应记录，无法判断是否需要检查。")
        sys.exit(1)

    status = record.get('anjianywzt') or ''
    timestamp_str = record.get('timestamp')
    last_update_time = parse_timestamp(timestamp_str)
    last_update_display = (
        last_update_time.strftime('%Y-%m-%d %H:%M:%S UTC')
        if last_update_time else 'N/A'
    )

    print(f"\n最新状态: {status or 'N/A'}")
    print(f"上次检查: {last_update_display}")

    if status not in status_breakdown:
        print("\n结论: 现在不由本策略检查")
        if status == '驳回等复审请求':
            print("原因: 已经进入目标状态「驳回等复审请求」，退出驳回前检查清单。")
        else:
            print("原因: 当前状态不在 data/focus_strategy.json 的驳回前检查规则中。")
        print("=" * 100)
        return

    info = status_breakdown[status]
    freq_days = info['frequency_days']
    freq_name = info.get('frequency_name', f'{freq_days}天检查')
    needs_update, days_until, next_update_time = calculate_needs_update(
        last_update_time,
        freq_days
    )
    days_since = (utc_now() - last_update_time).days if last_update_time else None
    next_update_display = (
        next_update_time.strftime('%Y-%m-%d %H:%M:%S UTC')
        if next_update_time else 'N/A'
    )

    print(f"内部规则: {freq_name}（{freq_days}天）")
    if days_since is not None:
        print(f"距上次检查: {days_since} 天")
    print(f"应检查时间: {next_update_display}")

    if needs_update:
        print("\n结论: 现在要检查状态")
        print("原因: 当前状态仍在驳回前检查范围内，且已达到检查间隔。")
    else:
        print("\n结论: 现在暂时不用检查状态")
        print(f"原因: 当前状态仍需跟踪，但还没到下一次检查时间；约 {days_until} 天后再检查。")

    print("=" * 100)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法:")
        print(f"  python {sys.argv[0]} <命令> [选项]")
        print(f"\n【基础命令】")
        print(f"  stats              - 显示策略统计信息")
        print(f"  status             - 显示所有申请号的更新状态")
        print(f"  status 7/14/30/45  - 显示指定周期的申请号状态")
        print(f"  check <申请号>     - 判断单个申请号现在是否需要检查状态")
        print(f"  generate           - 生成所有现在需要检查状态的申请号列表")
        print(f"  generate 7/14/30/45- 生成指定周期的申请号列表")
        print(f"\n【采集更新流程】")
        print(f"  prepare            - 采集前：保存当前状态到 previous_status ⭐")
        print(f"  # 然后执行 MITM 采集")
        print(f"  diff               - 采集后：显示申请号的状态变化（before/after）")
        print(f"\n【验证与分析命令】")
        print(f"  validate           - 验证 focus_strategy 计数与 detection_log 一致性")
        print(f"  report             - 生成详细的多维度统计报告")
        sys.exit(1)

    command = sys.argv[1]

    if command == 'stats':
        show_statistics()
    elif command == 'status':
        if len(sys.argv) > 2:
            try:
                frequency_days = int(sys.argv[2])
                show_update_status(frequency_days)
            except ValueError:
                print(f"❌ 频率必须是数字")
                sys.exit(1)
        else:
            show_update_status()
    elif command == 'validate':
        validate_focus_strategy()
    elif command == 'prepare':
        prepare_for_update()
    elif command == 'report':
        show_detailed_report()
    elif command == 'diff':
        show_status_changes()
    elif command == 'generate':
        if len(sys.argv) > 2:
            try:
                frequency_days = int(sys.argv[2])
                write_update_list_file(frequency_days)
            except ValueError:
                print(f"❌ 频率必须是数字")
                sys.exit(1)
        else:
            write_update_list_file()
    elif command == 'check':
        if len(sys.argv) < 3:
            print("❌ 请提供申请号")
            print(f"用法: python {sys.argv[0]} check <申请号>")
            sys.exit(1)
        check_application(sys.argv[2])
    else:
        print(f"❌ 未知的命令: {command}")
        sys.exit(1)
