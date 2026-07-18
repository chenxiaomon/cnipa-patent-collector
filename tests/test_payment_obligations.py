#!/usr/bin/env python3

import unittest
from datetime import date, datetime

from payment_obligations import (
    LATE_FEE_APPLICABLE,
    LATE_FEE_INVALID_INTERVAL,
    LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS,
    LATE_FEE_NOT_COLLECTED,
    LATE_FEE_NO_APPLICABLE_BRACKET,
    LATE_FEE_NO_SCHEDULE,
    build_current_late_fee_analysis,
    build_payable_fee_analysis,
)


def patent_record(application_no, payable_fees=None, late_fee_schedules=None):
    return {
        "application_no": application_no,
        "zhuanlimc": f"专利 {application_no}",
        "shenqingrxm": "测试申请人",
        "zhuanlilx": "实用新型",
        "anjianywzt": "专利权维持",
        "payable_fee_records": payable_fees,
        "late_fee_schedule_records": late_fee_schedules,
        "fee_snapshot_at": "2026-07-18T01:02:03Z",
    }


def payable_fee(deadline, status="未缴", amount="1200", fee_name="第6年年费"):
    return {
        "yingjiaoffyzlmc": fee_name,
        "yingjiaoje": amount,
        "jiaofeijzr": deadline,
        "yingjiaoffyzt": status,
    }


def late_fee_schedule(period, annual_fee="1200", late_fee="60", total="1260"):
    return {
        "zhinajjfsj": period,
        "zhinajdqnfje": annual_fee,
        "zhinajyjznje": late_fee,
        "zhinajzj": total,
    }


class TestPayableFeeAnalysis(unittest.TestCase):
    def test_emits_only_exact_unpaid_status_and_preserves_fields(self):
        records = [patent_record("202022888632X", [
            payable_fee("2026-09-09", amount="1000", fee_name="恢复权利请求费"),
            payable_fee("2027-01-04", status="已缴"),
            payable_fee("2027-01-05", status=" 未缴"),
        ])]

        rows = build_payable_fee_analysis(records, date(2026, 8, 10))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["application_no"], "202022888632X")
        self.assertEqual(rows[0]["yingjiaoffyzlmc"], "恢复权利请求费")
        self.assertEqual(rows[0]["yingjiaoje"], "1000")
        self.assertEqual(rows[0]["days_to_deadline"], 30)
        self.assertEqual(rows[0]["deadline_bucket"], "30天内")
        self.assertEqual(rows[0]["analysis_date"], "2026-08-10")
        self.assertEqual(rows[0]["fee_snapshot_at"], "2026-07-18T01:02:03Z")

    def test_classifies_deadlines_and_sorts_unknown_last(self):
        records = [
            patent_record("B", [payable_fee("bad-date", fee_name="未知")]),
            patent_record("D", [payable_fee("2026-09-01", fee_name="未来")]),
            patent_record("C", [payable_fee("2026-08-31", fee_name="三十天")]),
            patent_record("A", [
                payable_fee("2026-08-01", fee_name="今天"),
                payable_fee("2026-07-31", fee_name="逾期"),
            ]),
        ]

        rows = build_payable_fee_analysis(records, date(2026, 8, 1))

        self.assertEqual(
            [(row["application_no"], row["deadline_bucket"]) for row in rows],
            [
                ("A", "已逾期"),
                ("A", "今日截止"),
                ("C", "30天内"),
                ("D", "未来"),
                ("B", "日期未知"),
            ],
        )
        self.assertIsNone(rows[-1]["days_to_deadline"])

    def test_none_and_empty_payable_lists_emit_no_rows(self):
        records = [patent_record("A", None), patent_record("B", [])]
        self.assertEqual(build_payable_fee_analysis(records, date(2026, 8, 1)), [])

    def test_as_of_rejects_datetime(self):
        with self.assertRaises(TypeError):
            build_payable_fee_analysis([], datetime(2026, 8, 1))


class TestCurrentLateFeeAnalysis(unittest.TestCase):
    def test_selects_one_chinese_interval_inclusively_without_summing(self):
        schedules = [
            late_fee_schedule("2026年01月06日到2026年02月03日", late_fee="60", total="1260"),
            late_fee_schedule("2026年02月04日到2026年03月03日", late_fee="120", total="1320"),
            late_fee_schedule("2026年03月04日到2026年04月03日", late_fee="180", total="1380"),
            late_fee_schedule("2026年04月04日到2026年05月06日", late_fee="240", total="1440"),
            late_fee_schedule("2026年05月07日到2026年06月03日", late_fee="300", total="1500"),
        ]

        row = build_current_late_fee_analysis(
            [patent_record("A", late_fee_schedules=schedules)],
            date(2026, 2, 3),
        )[0]

        self.assertEqual(row["late_fee_analysis_status"], LATE_FEE_APPLICABLE)
        self.assertEqual(row["zhinajjfsj"], schedules[0]["zhinajjfsj"])
        self.assertEqual(row["zhinajdqnfje"], "1200")
        self.assertEqual(row["zhinajyjznje"], "60")
        self.assertEqual(row["zhinajzj"], "1260")
        self.assertEqual(row["analysis_date"], "2026-02-03")
        self.assertEqual(row["interval_start"], "2026-01-06")
        self.assertEqual(row["interval_end"], "2026-02-03")
        self.assertEqual(row["applicable_bracket_count"], 1)

    def test_accepts_iso_intervals_with_both_delimiters(self):
        records = [
            patent_record("B", late_fee_schedules=[
                late_fee_schedule("2026-02-04至2026-03-03", late_fee=120),
            ]),
            patent_record("A", late_fee_schedules=[
                late_fee_schedule("2026-01-06到2026-02-03", late_fee=60),
            ]),
        ]

        rows = build_current_late_fee_analysis(records, date(2026, 2, 4))

        self.assertEqual([row["application_no"] for row in rows], ["A", "B"])
        self.assertEqual(rows[0]["late_fee_analysis_status"], LATE_FEE_NO_APPLICABLE_BRACKET)
        self.assertEqual(rows[1]["late_fee_analysis_status"], LATE_FEE_APPLICABLE)
        self.assertEqual(rows[1]["zhinajyjznje"], 120)

    def test_distinguishes_null_empty_no_match_and_invalid_intervals(self):
        rows = build_current_late_fee_analysis([
            patent_record("A", late_fee_schedules=None),
            patent_record("B", late_fee_schedules=[]),
            patent_record("C", late_fee_schedules=[
                late_fee_schedule("2026-01-01到2026-01-31"),
            ]),
            patent_record("D", late_fee_schedules=[
                late_fee_schedule("not-an-interval"),
                late_fee_schedule("2026-03-03到2026-02-04"),
            ]),
        ], date(2026, 2, 4))

        self.assertEqual(
            [row["late_fee_analysis_status"] for row in rows],
            [
                LATE_FEE_NOT_COLLECTED,
                LATE_FEE_NO_SCHEDULE,
                LATE_FEE_NO_APPLICABLE_BRACKET,
                LATE_FEE_INVALID_INTERVAL,
            ],
        )
        self.assertEqual(rows[-1]["invalid_interval_count"], 2)
        self.assertTrue(all(row["zhinajzj"] is None for row in rows))

    def test_reports_overlap_instead_of_selecting_or_summing(self):
        schedules = [
            late_fee_schedule("2026-01-01至2026-02-15", late_fee="60", total="1260"),
            late_fee_schedule("2026-02-01至2026-02-28", late_fee="120", total="1320"),
        ]

        row = build_current_late_fee_analysis(
            [patent_record("A", late_fee_schedules=schedules)],
            date(2026, 2, 4),
        )[0]

        self.assertEqual(
            row["late_fee_analysis_status"],
            LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS,
        )
        self.assertEqual(row["applicable_bracket_count"], 2)
        self.assertIsNone(row["zhinajyjznje"])
        self.assertIsNone(row["zhinajzj"])

    def test_valid_match_wins_while_bad_intervals_remain_counted(self):
        schedules = [
            late_fee_schedule("bad"),
            late_fee_schedule("2026-02-01至2026-02-28", late_fee="120"),
        ]

        row = build_current_late_fee_analysis(
            [patent_record("A", late_fee_schedules=schedules)],
            date(2026, 2, 4),
        )[0]

        self.assertEqual(row["late_fee_analysis_status"], LATE_FEE_APPLICABLE)
        self.assertEqual(row["invalid_interval_count"], 1)
        self.assertEqual(row["zhinajyjznje"], "120")


if __name__ == "__main__":
    unittest.main()
