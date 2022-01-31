import ssg

def test_chunking():
    assert ssg.chunk_stable(list(range(0)), 5, 3) == []
    assert ssg.chunk_stable(list(range(1)), 5, 3) == [[0]]
    assert ssg.chunk_stable(list(range(3)), 5, 3) == [[0,1,2]]
    assert ssg.chunk_stable(list(range(5)), 5, 3) == [[0,1,2,3,4]]
    assert ssg.chunk_stable(list(range(6)), 5, 3) == [[0, 1,2,3,4,5]]
    assert ssg.chunk_stable(list(range(7)), 5, 3) == [[0,1, 2,3,4,5,6]]
    # first separation:
    assert ssg.chunk_stable(list(range(8)), 5, 3) == [[0,1,2], [3,4,5,6,7]]
    assert ssg.chunk_stable(list(range(11)), 5, 3) == [[0, 1,2,3,4,5], [6,7,8,9,10]]
    assert ssg.chunk_stable(list(range(13)), 5, 3) == [[0,1,2], [3,4,5,6,7], [8,9,10,11,12]]
