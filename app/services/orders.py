"""Order operations: placing, paying and cancelling purchases."""
from datetime import datetime
from typing import Dict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Book, Member, MemberTier, Order, OrderItem, OrderStatus
from app.schemas import OrderCreate
from app.services.members import ensure_can_access_restricted

# Percentage discount granted by each membership tier.
TIER_DISCOUNT_PERCENT: Dict[str, int] = {
    MemberTier.APPRENTICE.value: 0,
    MemberTier.ADEPT.value: 5,
    MemberTier.MASTER.value: 10,
    MemberTier.SUPREME.value: 15,
}

# Extra discount when the total quantity across all items reaches the threshold.
BULK_QUANTITY_THRESHOLD = 10
BULK_DISCOUNT_PERCENT = 5


def calculate_discount_percent(member: Member, total_quantity: int) -> int:
    """Tier discount, plus the bulk discount when total quantity >= threshold."""
    discount = TIER_DISCOUNT_PERCENT.get(member.tier, 0)
    if total_quantity >= BULK_QUANTITY_THRESHOLD:
        discount += BULK_DISCOUNT_PERCENT
    return discount


def create_order(db: Session, data: OrderCreate, now: datetime) -> Order:
    """Place a pending order and reserve stock.

    Checks, in order (422 for empty items / bad quantity / duplicate books is done by the schema):
    1. 404 member not found; 404 any book not found
    2. 403 any book restricted and member tier below master
    3. 409 any book has insufficient stock (all-or-nothing: nothing is changed)
    Then stock is decremented for every item and prices are snapshotted.
    Pricing: discount_cents = subtotal * percent // 100; total = subtotal - discount.
    
    Uses SELECT FOR UPDATE to lock book rows and prevent concurrent order race conditions.
    """
    member = db.get(Member, data.member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    # Lock book rows in consistent order (by id) to prevent deadlocks
    book_ids = sorted([item.book_id for item in data.items])
    books_query = select(Book).where(Book.id.in_(book_ids)).order_by(Book.id).with_for_update()
    locked_books = {book.id: book for book in db.scalars(books_query)}

    # Verify all books exist (maintain original order for error messages)
    books = []
    for item in data.items:
        book = locked_books.get(item.book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"Book with id {item.book_id} not found")
        books.append(book)

    if any(book.restricted for book in books):
        ensure_can_access_restricted(member)

    for item, book in zip(data.items, books):
        if book.stock < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for book '{book.title}': requested {item.quantity}, available {book.stock}",
            )

    order_items = []
    total_quantity = 0
    subtotal_cents = 0

    for item, book in zip(data.items, books):
        book.stock -= item.quantity
        unit_price = book.price_cents
        subtotal_cents += unit_price * item.quantity
        total_quantity += item.quantity
        order_items.append(
            OrderItem(
                book_id=book.id,
                quantity=item.quantity,
                unit_price_cents=unit_price,
            )
        )

    discount_percent = calculate_discount_percent(member, total_quantity)
    discount_cents = subtotal_cents * discount_percent // 100
    total_cents = subtotal_cents - discount_cents

    order = Order(
        member_id=member.id,
        status=OrderStatus.PENDING.value,
        subtotal_cents=subtotal_cents,
        discount_percent=discount_percent,
        discount_cents=discount_cents,
        total_cents=total_cents,
        created_at=now,
        items=order_items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> Order:
    """Return an order by id, or raise 404."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def pay_order(db: Session, order_id: int) -> Order:
    """Mark a pending order as paid. 404 if missing; 409 if not pending."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"Cannot pay an order that is {order.status}")
    order.status = OrderStatus.PAID.value
    db.commit()
    db.refresh(order)
    return order


def cancel_order(db: Session, order_id: int) -> Order:
    """Cancel a pending order and restore the reserved stock. 404 if missing; 409 if not pending."""
    order = get_order(db, order_id)
    if order.status != OrderStatus.PENDING.value:
        raise HTTPException(status_code=409, detail=f"Cannot cancel an order that is {order.status}")
    for item in order.items:
        if item.book is not None:
            item.book.stock += item.quantity
    order.status = OrderStatus.CANCELLED.value
    db.commit()
    db.refresh(order)
    return order
