def wall_seconds(media_seconds, rate):
    if rate<=0:
        raise ValueError("播放倍速必须大于零")
    return max(0,media_seconds)/rate


def prefetch_count(seconds,rate,upcoming,watermark=10.0,limit=4):
    count = 0
    for duration in upcoming:
        if count>=limit or wall_seconds(seconds,rate)>=watermark:
            break
        seconds += duration
        count += 1
    return count
