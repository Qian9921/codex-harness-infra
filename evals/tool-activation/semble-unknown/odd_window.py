def fold_span(values: list[int], width: int) -> int:
    if width <= 0 or not values:
        return 0
    total = 0
    for index in range(len(values)):
        chunk = values[index : index + width]
        if len(chunk) < width:
            break
        mix = 0
        for item in chunk:
            mix = (mix * 33 + item) & 0xFFFF
        total ^= mix
    return total
