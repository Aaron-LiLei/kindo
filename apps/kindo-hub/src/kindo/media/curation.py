"""系列/课程归组领域操作（扫描器 sidecar 路径与家长修正 PATCH 共用）。

归组事实来源优先级（§7.4）：sidecar < 家长修正（parent_edited_json，重扫不覆盖）。
本模块只做行的建立/改绑/解除，不决定优先级——由调用方（scanner/admin）控制顺序。
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Course, Episode, Lesson, Media, Series
from ..util import new_id


def get_or_create_series(session: Session, title: str, language: str | None = None) -> Series:
    series = session.query(Series).filter(Series.title == title).one_or_none()
    if series is not None:
        return series
    series = Series(id=new_id(), title=title, language=language)
    session.add(series)
    # 并发扫描同名系列触发唯一约束时用 savepoint 回退并复用已存在行
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        series = session.query(Series).filter(Series.title == title).one()
    return series


def get_or_create_course(session: Session, title: str, language: str | None = None) -> Course:
    course = session.query(Course).filter(Course.title == title).one_or_none()
    if course is not None:
        return course
    course = Course(id=new_id(), title=title, language=language)
    session.add(course)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        course = session.query(Course).filter(Course.title == title).one()
    return course


def upsert_episode(session: Session, media: Media, series_name: str,
                   season_no: int | None = None, episode_no: int | None = None) -> None:
    """把媒体绑定到系列（不存在则建）；season/episode 缺省时保留现有值或取 1。"""
    series = get_or_create_series(session, series_name, media.language)
    ep = session.query(Episode).filter(Episode.media_id == media.id).one_or_none()
    if ep is None:
        session.add(Episode(
            id=new_id(), series_id=series.id,
            season_no=season_no or 1, episode_no=episode_no or 1,
            media_id=media.id, title=media.title,
        ))
    else:
        ep.series_id = series.id
        ep.season_no = season_no or ep.season_no
        ep.episode_no = episode_no or ep.episode_no
        ep.title = media.title


def remove_episode(session: Session, media: Media) -> None:
    session.query(Episode).filter(Episode.media_id == media.id).delete()


def upsert_lesson(session: Session, media: Media, course_name: str,
                  chapter_no: int | None = None, lesson_no: int | None = None) -> None:
    course = get_or_create_course(session, course_name, media.language)
    lesson = session.query(Lesson).filter(Lesson.media_id == media.id).one_or_none()
    if lesson is None:
        session.add(Lesson(
            id=new_id(), course_id=course.id,
            chapter_no=chapter_no or 1, lesson_no=lesson_no or 1,
            media_id=media.id, title=media.title,
        ))
    else:
        lesson.course_id = course.id
        lesson.chapter_no = chapter_no or lesson.chapter_no
        lesson.lesson_no = lesson_no or lesson.lesson_no
        lesson.title = media.title


def remove_lesson(session: Session, media: Media) -> None:
    session.query(Lesson).filter(Lesson.media_id == media.id).delete()
