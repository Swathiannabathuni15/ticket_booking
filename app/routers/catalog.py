"""Movies, theatres and screens: admin-created catalogue data. Showtimes
(which actually generate bookable seats) live in app/routers/showtimes.py.
"""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import Movie, Screen, Showtime, Theatre, User
from app.schemas import MovieCreate, MovieOut, MovieUpdate, ScreenCreate, ScreenOut, TheatreCreate, TheatreOut

router = APIRouter(tags=["catalog"])


@router.post("/movies", response_model=MovieOut, status_code=status.HTTP_201_CREATED)
def create_movie(body: MovieCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    movie = Movie(**body.model_dump())
    db.add(movie)
    db.commit()
    return movie


@router.patch("/movies/{movie_id}", response_model=MovieOut)
def update_movie(
    movie_id: int, body: MovieUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    movie = db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Movie not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(movie, field, value)
    db.commit()
    return movie


@router.get("/movies", response_model=list[MovieOut])
def list_movies(
    limit: int = 20,
    offset: int = 0,
    city: str | None = None,
    status: str | None = Query(None, pattern="^(now_showing|coming_soon)$"),
    db: Session = Depends(get_db),
):
    """With ?city=X only movies that have a show still to come in a theatre in that city.
    ?status=now_showing hides movies with a future release date; ?status=coming_soon returns
    only those (in every city: they have no shows yet)."""
    limit = max(1, min(limit, 100))
    today = date.today()
    stmt = (
        select(Movie).order_by(Movie.release_date, Movie.title)
        if status == "coming_soon"
        else select(Movie).order_by(Movie.title)
    )
    stmt = stmt.limit(limit).offset(max(offset, 0))
    if status == "coming_soon":
        stmt = stmt.where(Movie.release_date > today)
        return db.scalars(stmt).all()
    if status == "now_showing":
        stmt = stmt.where((Movie.release_date.is_(None)) | (Movie.release_date <= today))
    if city:
        stmt = stmt.where(
            Movie.id.in_(
                select(Showtime.movie_id)
                .join(Screen, Screen.id == Showtime.screen_id)
                .join(Theatre, Theatre.id == Screen.theatre_id)
                .where(func.lower(Theatre.city) == city.lower(), Showtime.starts_at > datetime.now(UTC))
            )
        )
    return db.scalars(stmt).all()


@router.get("/movies/{movie_id}", response_model=MovieOut)
def get_movie(movie_id: int, db: Session = Depends(get_db)):
    movie = db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Movie not found")
    return movie


@router.get("/cities", response_model=list[str])
def list_cities(db: Session = Depends(get_db)):
    """Cities that have at least one theatre."""
    return db.scalars(select(Theatre.city).distinct().order_by(Theatre.city)).all()


@router.post("/theatres", response_model=TheatreOut, status_code=status.HTTP_201_CREATED)
def create_theatre(body: TheatreCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    theatre = Theatre(name=body.name, city=body.city)
    db.add(theatre)
    db.commit()
    return theatre


@router.get("/theatres", response_model=list[TheatreOut])
def list_theatres(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    stmt = select(Theatre).order_by(Theatre.name).limit(limit).offset(max(offset, 0))
    return db.scalars(stmt).all()


@router.post("/screens", response_model=ScreenOut, status_code=status.HTTP_201_CREATED)
def create_screen(body: ScreenCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    if db.get(Theatre, body.theatre_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Theatre not found")
    layout = [{"row": row.row, "seats": row.seats} for row in body.rows]
    screen = Screen(theatre_id=body.theatre_id, name=body.name, layout=layout)
    db.add(screen)
    db.commit()
    return screen


@router.get("/screens", response_model=list[ScreenOut])
def list_screens(db: Session = Depends(get_db)):
    return db.scalars(select(Screen).order_by(Screen.id)).all()
