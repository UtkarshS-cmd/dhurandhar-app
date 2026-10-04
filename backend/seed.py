
from datetime import date, timedelta, time
from sqlalchemy import select
from app.db import SessionLocal
from app.models import *
import json

CITIES = ["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Kolkata","Pune","Ahmedabad","Shimla","Chandigarh"]
THEATER_NAMES = ["PVR Luxe", "INOX Megaplex"]
TIMES = [time(11,30), time(15,0), time(18,30), time(21,45)]

def seed():
    db=SessionLocal()
    try:
        movie=db.scalar(select(Movie).where(Movie.title=="Dhurandhar"))
        if not movie:
            movie=Movie(title="Dhurandhar",metadata_json=json.dumps({"director":"Aditya Dhar","language":"Hindi"}))
            db.add(movie); db.flush()
        for cname in CITIES:
            city=db.scalar(select(City).where(City.name==cname))
            if not city:
                city=City(name=cname); db.add(city); db.flush()
            for ti,tname in enumerate(THEATER_NAMES):
                theater=db.scalar(select(Theater).where(Theater.city_id==city.id,Theater.name==f"{tname} {cname}"))
                if not theater:
                    theater=Theater(city_id=city.id,name=f"{tname} {cname}",address=f"Central Cinema District, {cname}")
                    db.add(theater); db.flush()
                screen=db.scalar(select(Screen).where(Screen.theater_id==theater.id,Screen.name=="Screen 1"))
                if not screen:
                    screen=Screen(theater_id=theater.id,name="Screen 1"); db.add(screen); db.flush()
                    for row_i,row in enumerate(list("ABCDEFGH")):
                        cat = "Gold" if row in "AB" else "Silver" if row in "CDE" else "Bronze"
                        price = {"Gold":450,"Silver":350,"Bronze":200}[cat]
                        for n in range(1,11):
                            db.add(Seat(screen_id=screen.id,row_label=row,seat_number=n,category=cat,price=price))
                    db.flush()
                seats=db.scalars(select(Seat).where(Seat.screen_id==screen.id)).all()
                start=date.today()
                for day in range(7):
                    d=start+timedelta(days=day)
                    for st in TIMES:
                        show=db.scalar(select(Show).where(Show.screen_id==screen.id,Show.show_date==d,Show.show_time==st))
                        if not show:
                            show=Show(movie_id=movie.id,screen_id=screen.id,show_date=d,show_time=st,status="ACTIVE")
                            db.add(show); db.flush()
                            for seat in seats:
                                db.add(ShowSeat(show_id=show.id,seat_id=seat.id,status=ShowSeatStatus.AVAILABLE.value))
        db.commit()
        print("Seed complete")
    finally:
        db.close()

if __name__=="__main__":
    seed()
