if request.IsInit:
    value = 0
elif request.IsRead:
    request.Value = value
elif request.IsWrite:
    value = (request.Value & ~0xc) | ((request.Value & 3) << 2)
