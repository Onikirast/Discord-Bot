"""
tests/test_database.py

Every test here takes the `temp_db` fixture from conftest.py, which
points database.py at a fresh, throwaway SQLite file -- so these tests
never touch the real bot.db, and each test starts from a clean slate.
"""

from datetime import datetime, timedelta

import database


# ---------- Reminders ----------

async def test_add_and_list_reminder(temp_db):
    await database.add_reminder(
        user_id=1, channel_id=100, message="test reminder",
        remind_at=datetime.utcnow() + timedelta(hours=1),
    )
    reminders = await database.list_reminders(user_id=1)
    assert len(reminders) == 1
    assert reminders[0]["message"] == "test reminder"
    assert reminders[0]["status"] == "pending"


async def test_due_reminder_is_found_when_past_due(temp_db):
    await database.add_reminder(
        user_id=1, channel_id=100, message="overdue",
        remind_at=datetime.utcnow() - timedelta(minutes=5),
    )
    due = await database.get_due_reminders(datetime.utcnow())
    assert len(due) == 1
    assert due[0]["message"] == "overdue"


async def test_future_reminder_is_not_due_yet(temp_db):
    await database.add_reminder(
        user_id=1, channel_id=100, message="not yet",
        remind_at=datetime.utcnow() + timedelta(hours=1),
    )
    due = await database.get_due_reminders(datetime.utcnow())
    assert due == []


async def test_once_reminder_closes_after_firing(temp_db):
    reminder_id = await database.add_reminder(
        user_id=1, channel_id=100, message="one-off",
        remind_at=datetime.utcnow() - timedelta(minutes=1), recurrence="once",
    )
    await database.reschedule_or_close_reminder(reminder_id, "once", datetime.utcnow())
    # A closed 'once' reminder should no longer appear in the pending list.
    assert await database.list_reminders(user_id=1) == []


async def test_daily_reminder_reschedules_forward_one_day(temp_db):
    original_time = datetime.utcnow() - timedelta(minutes=1)
    reminder_id = await database.add_reminder(
        user_id=1, channel_id=100, message="daily", remind_at=original_time, recurrence="daily",
    )
    await database.reschedule_or_close_reminder(reminder_id, "daily", original_time)
    reminders = await database.list_reminders(user_id=1)
    assert len(reminders) == 1
    new_time = datetime.fromisoformat(reminders[0]["remind_at"])
    assert abs((new_time - (original_time + timedelta(days=1))).total_seconds()) < 2


async def test_delete_reminder(temp_db):
    reminder_id = await database.add_reminder(
        user_id=1, channel_id=100, message="delete me", remind_at=datetime.utcnow(),
    )
    assert await database.delete_reminder(user_id=1, reminder_id=reminder_id) is True
    assert await database.list_reminders(user_id=1) == []


async def test_delete_nonexistent_reminder_returns_false(temp_db):
    assert await database.delete_reminder(user_id=1, reminder_id=9999) is False


async def test_reminder_belongs_to_correct_user_only(temp_db):
    await database.add_reminder(user_id=1, channel_id=100, message="mine", remind_at=datetime.utcnow())
    await database.add_reminder(user_id=2, channel_id=100, message="not mine", remind_at=datetime.utcnow())
    assert len(await database.list_reminders(user_id=1)) == 1
    assert len(await database.list_reminders(user_id=2)) == 1


# ---------- Timetable ----------

async def test_add_and_list_timetable_entry(temp_db):
    await database.add_timetable_entry(
        user_id=1, channel_id=100, day_of_week=0, start_time="09:00", title="Test Class",
    )
    entries = await database.list_timetable(user_id=1)
    assert len(entries) == 1
    assert entries[0]["title"] == "Test Class"
    assert entries[0]["day_of_week"] == 0


async def test_get_entries_for_day_filters_correctly(temp_db):
    await database.add_timetable_entry(user_id=1, channel_id=100, day_of_week=0, start_time="09:00", title="Monday class")
    await database.add_timetable_entry(user_id=1, channel_id=100, day_of_week=2, start_time="09:00", title="Wednesday class")
    monday_entries = await database.get_entries_for_day(0)
    assert len(monday_entries) == 1
    assert monday_entries[0]["title"] == "Monday class"


async def test_delete_timetable_entry(temp_db):
    entry_id = await database.add_timetable_entry(
        user_id=1, channel_id=100, day_of_week=0, start_time="09:00", title="Delete me",
    )
    assert await database.delete_timetable_entry(user_id=1, entry_id=entry_id) is True
    assert await database.list_timetable(user_id=1) == []


# ---------- DCU course link ----------

async def test_set_and_get_dcu_link(temp_db):
    await database.set_dcu_link(user_id=1, course_name="COMSCI2", course_description=None, course_identity="abc-123")
    link = await database.get_dcu_link(user_id=1)
    assert link["course_name"] == "COMSCI2"
    assert link["course_identity"] == "abc-123"


async def test_relinking_updates_existing_link_not_duplicates(temp_db):
    await database.set_dcu_link(user_id=1, course_name="COMSCI1", course_description=None, course_identity="old-id")
    await database.set_dcu_link(user_id=1, course_name="COMSCI2", course_description=None, course_identity="new-id")
    link = await database.get_dcu_link(user_id=1)
    # Should have been updated in place, not left as the original or duplicated.
    assert link["course_name"] == "COMSCI2"
    assert link["course_identity"] == "new-id"


async def test_get_dcu_link_returns_none_when_unlinked(temp_db):
    assert await database.get_dcu_link(user_id=999) is None


async def test_delete_dcu_link(temp_db):
    await database.set_dcu_link(user_id=1, course_name="COMSCI2", course_description=None, course_identity="abc-123")
    assert await database.delete_dcu_link(user_id=1) is True
    assert await database.get_dcu_link(user_id=1) is None


# ---------- Grades ----------

async def test_add_and_list_grade(temp_db):
    await database.add_grade(user_id=1, module_name="CSC1018[2] Logic", grade=72.0, credits=5.0)
    grades = await database.list_grades(user_id=1)
    assert len(grades) == 1
    assert grades[0]["grade"] == 72.0
    assert grades[0]["credits"] == 5.0


async def test_list_grades_filters_by_semester(temp_db):
    await database.add_grade(user_id=1, module_name="A", grade=70.0, semester="Sem 1")
    await database.add_grade(user_id=1, module_name="B", grade=60.0, semester="Sem 2")
    sem1_only = await database.list_grades(user_id=1, semester="Sem 1")
    assert len(sem1_only) == 1
    assert sem1_only[0]["module_name"] == "A"


async def test_delete_grade(temp_db):
    grade_id = await database.add_grade(user_id=1, module_name="Delete me", grade=50.0)
    assert await database.delete_grade(user_id=1, grade_id=grade_id) is True
    assert await database.list_grades(user_id=1) == []


async def test_grade_ids_are_never_reused_after_delete(temp_db):
    # Documents the AUTOINCREMENT behavior discussed during manual testing:
    # deleting an entry does not free up its ID for reuse.
    first_id = await database.add_grade(user_id=1, module_name="First", grade=70.0)
    await database.delete_grade(user_id=1, grade_id=first_id)
    second_id = await database.add_grade(user_id=1, module_name="Second", grade=80.0)
    assert second_id != first_id
