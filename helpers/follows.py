"""
Khách theo dõi CTV — dùng chung cho nút Bật/Tắt thông báo ở panel server và nút
Tắt thông báo trong DM, nên 2 nút luôn cho kết quả giống nhau.

Lưu trong data_ctv_subs.json: {ctv_id: [customer_id, ...]}

- Panel (khách chọn 1 kênh): Bật/Tắt = theo dõi / bỏ theo dõi các CTV chủ kênh đó.
- DM báo live của CTV X: Tắt = bỏ theo dõi CTV X.
- CTV X bật live ở BẤT KỲ kênh nào → mọi khách theo dõi X nhận DM.
"""
from core.data_manager import db

KEY = "ctv_subscribers"


def _ids(values) -> list[str]:
    return [str(v) for v in values]


def followers_of(ctv_id) -> list[str]:
    data = db.load(KEY)
    lst = data.get(str(ctv_id), [])
    return list(dict.fromkeys(_ids(lst))) if isinstance(lst, list) else []


def following(customer_id, ctv_ids) -> list[str]:
    """Các CTV (trong `ctv_ids`) mà khách đang theo dõi."""
    data = db.load(KEY)
    cid = str(customer_id)
    return [
        str(x) for x in ctv_ids
        if isinstance(data.get(str(x)), list) and cid in _ids(data[str(x)])
    ]


def count_followers(ctv_ids) -> int:
    """Số khách (không trùng) đang theo dõi ít nhất 1 CTV trong `ctv_ids`."""
    data = db.load(KEY)
    people: set[str] = set()
    for x in ctv_ids:
        lst = data.get(str(x))
        if isinstance(lst, list):
            people.update(_ids(lst))
    return len(people)


async def follow(customer_id, ctv_ids) -> list[str]:
    """Theo dõi các CTV. Trả về các CTV vừa được thêm."""
    cid = str(customer_id)
    added: list[str] = []

    def _save(data):
        for x in {str(v) for v in ctv_ids}:
            lst = data.setdefault(x, [])
            if cid not in _ids(lst):
                lst.append(cid)
                added.append(x)

    await db.aupdate(KEY, _save)
    return added


async def unfollow(customer_id, ctv_ids) -> list[str]:
    """Bỏ theo dõi các CTV. Trả về các CTV vừa được bỏ."""
    cid = str(customer_id)
    removed: list[str] = []

    def _save(data):
        for x in {str(v) for v in ctv_ids}:
            lst = data.get(x)
            if not isinstance(lst, list):
                continue
            kept = [u for u in lst if str(u) != cid]
            if len(kept) != len(lst):
                data[x] = kept
                removed.append(x)

    await db.aupdate(KEY, _save)
    return removed
