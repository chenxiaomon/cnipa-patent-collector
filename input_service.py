#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
输入服务 - 统一处理 PyAutoGUI 鼠标操作和文本输入
"""

import time
import random

import pyautogui

from browser_utils import clear_input_field, real_type


class InputService:
    """统一封装 PyAutoGUI 鼠标移动、点击和文本输入操作"""

    @staticmethod
    def move_and_click(x: int, y: int, post_click_wait: float = None) -> None:
        """
        移动鼠标到 (x, y) 并点击

        Args:
            x, y: 目标坐标
            post_click_wait: 点击后额外等待秒数（None 则不等待）
        """
        pyautogui.moveTo(x, y, duration=random.uniform(0.3, 0.5))
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click()
        if post_click_wait:
            time.sleep(post_click_wait)

    @staticmethod
    def type_in_search(
        input_x: int, input_y: int,
        button_x: int, button_y: int,
        text: str,
        delay_range: tuple = (0.03, 0.08),
        pause_prob: float = 0.08,
        post_search_wait: float = None,
    ) -> None:
        """
        完整搜索流程：点击输入框 → 清空 → 输入文本 → 点击查询按钮

        Args:
            input_x, input_y: 输入框坐标
            button_x, button_y: 查询按钮坐标
            text: 要输入的文本
            delay_range: real_type 字符间延迟范围
            pause_prob: real_type 随机长延迟概率
            post_search_wait: 点击查询后额外等待秒数
        """
        InputService.move_and_click(input_x, input_y, post_click_wait=0.5)
        clear_input_field()
        real_type(text, delay_range=delay_range, pause_prob=pause_prob)
        time.sleep(random.uniform(0.5, 1))
        InputService.move_and_click(button_x, button_y)
        if post_search_wait:
            time.sleep(post_search_wait)
