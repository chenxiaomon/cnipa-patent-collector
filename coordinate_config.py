"""Coordinate configuration rules shared by recording, API saves, and diagnostics."""


SEARCH_COORDINATE_PAIRS = (('input_x', 'input_y'), ('button_x', 'button_y'))
DETAIL_LINK_COORDINATE_PAIRS = (('link_x', 'link_y'),)
FEE_MENU_COORDINATE_PAIRS = (('fee_menu_x', 'fee_menu_y'),)
FWXX_COORDINATE_PAIRS = DETAIL_LINK_COORDINATE_PAIRS + (('fwxx_menu_x', 'fwxx_menu_y'),)
DETAIL_COORDINATE_PAIRS = FWXX_COORDINATE_PAIRS + FEE_MENU_COORDINATE_PAIRS


def _coordinate_problems(payload: object, coordinate_pairs: tuple) -> tuple[list[str], list[str]]:
    if not isinstance(payload, dict):
        return ['坐标配置必须是 JSON 对象'], []

    invalid_fields = []
    missing_fields = []
    for coordinate_x, coordinate_y in coordinate_pairs:
        coordinate_keys = (coordinate_x, coordinate_y)
        invalid_keys = [
            key for key in coordinate_keys
            if key in payload and type(payload[key]) is not int
        ]
        if invalid_keys:
            invalid_fields.append('、'.join(invalid_keys) + ' 必须是整数，不能使用字符串、布尔值或小数')
        if any(key not in payload for key in coordinate_keys):
            missing_fields.append(coordinate_x.removesuffix('_x') + ' 坐标缺失，需要录制')
        elif not invalid_keys and (payload[coordinate_x], payload[coordinate_y]) == (0, 0):
            invalid_fields.append(coordinate_x.removesuffix('_x') + ' 仍是 (0, 0) 占位值，需要重新录制')
    return invalid_fields, missing_fields


def validate_coordinate_config(payload: object) -> None:
    """Accept missing coordinates for later recording, but reject malformed supplied values."""
    invalid_fields, _ = _coordinate_problems(payload, SEARCH_COORDINATE_PAIRS + DETAIL_COORDINATE_PAIRS)
    if invalid_fields:
        raise ValueError('；'.join(invalid_fields))


def coordinate_configuration_issues(payload: object, coordinate_pairs: tuple) -> list[str]:
    """Describe invalid or unrecorded coordinates required by a particular operation."""
    invalid_fields, missing_fields = _coordinate_problems(payload, coordinate_pairs)
    return invalid_fields + missing_fields


def recorded_coordinates(payload: object, coordinate_pairs: tuple) -> tuple[int, ...] | None:
    """Return complete usable coordinate pairs, or request recording with None."""
    if coordinate_configuration_issues(payload, coordinate_pairs):
        return None
    return tuple(payload[key] for pair in coordinate_pairs for key in pair)
