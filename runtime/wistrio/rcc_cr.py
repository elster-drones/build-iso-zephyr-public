if request.IsInit:
    value = 0x300
elif request.IsRead:
    request.Value = value
elif request.IsWrite:
    value = request.Value
    for shift in (0, 8, 16, 24):
        value = (value & ~(2 << shift)) | ((value & (1 << shift)) << 1)
