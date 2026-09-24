"""Reporting queries."""
from typing import List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Book, Order, OrderItem, OrderStatus
from app.schemas import TopBook


def top_books(db: Session, limit: int = 5) -> List[TopBook]:
    """Best-selling books.

    Rules: copies_sold sums quantities over ``paid`` orders only; books with no sales are
    excluded; sorted by copies_sold desc, then title asc; at most ``limit`` rows.
    """
    rows = db.execute(
        select(
            OrderItem.book_id,
            Book.title,
            func.sum(OrderItem.quantity).label("copies_sold"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .join(Book, OrderItem.book_id == Book.id)
        .where(Order.status == OrderStatus.PAID.value)
        .group_by(OrderItem.book_id, Book.title)
        .order_by(func.sum(OrderItem.quantity).desc(), Book.title.asc())
        .limit(limit)
    ).all()

    return [
        TopBook(book_id=row.book_id, title=row.title, copies_sold=row.copies_sold)
        for row in rows
    ]
