"""Build the Olist warehouse CSVs (data/csv/) from the raw Kaggle files (data/raw/olist/).

Source: Olist, "Brazilian E-Commerce Public Dataset by Olist", Kaggle (olistbr/brazilian-ecommerce),
licence CC BY-NC-SA 4.0. Nine raw tables -> a star schema with one golden customer record per person,
order-level derived fields (delivery days, lateness, main payment type, latest review) and a geolocation
table collapsed from 1M points to one row per zip prefix.

    python data/build_olist.py            # reads data/raw/olist/*.csv, writes data/csv/*.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw" / "olist"
OUT = ROOT / "csv"

REGION = {
    **dict.fromkeys(["SP", "RJ", "MG", "ES"], "Southeast"),
    **dict.fromkeys(["PR", "SC", "RS"], "South"),
    **dict.fromkeys(["DF", "GO", "MT", "MS"], "Center-West"),
    **dict.fromkeys(["BA", "SE", "AL", "PE", "PB", "RN", "CE", "PI", "MA"], "Northeast"),
    **dict.fromkeys(["AM", "PA", "RO", "RR", "AP", "AC", "TO"], "North"),
}
EXTRA_TRANSLATIONS = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "kitchen_portables_and_food_preparers",
}
CATEGORY_GROUP = {
    "Home & Furniture": [
        "bed_bath_table",
        "furniture_decor",
        "housewares",
        "home_confort",
        "home_comfort_2",
        "furniture_living_room",
        "furniture_bedroom",
        "furniture_mattress_and_upholstery",
        "kitchen_dining_laundry_garden_furniture",
        "office_furniture",
        "home_appliances",
        "home_appliances_2",
        "small_appliances",
        "small_appliances_home_oven_and_coffee",
        "air_conditioning",
        "la_cuisine",
        "kitchen_portables_and_food_preparers",
        "home_construction",
        "construction_tools_construction",
        "construction_tools_lights",
        "construction_tools_safety",
        "costruction_tools_garden",
        "costruction_tools_tools",
        "garden_tools",
    ],
    "Beauty & Health": ["health_beauty", "perfumery", "diapers_and_hygiene"],
    "Electronics": [
        "computers_accessories",
        "telephony",
        "fixed_telephony",
        "electronics",
        "computers",
        "pc_gamer",
        "audio",
        "tablets_printing_image",
        "consoles_games",
        "cine_photo",
        "dvds_blu_ray",
        "music",
        "signaling_and_security",
        "security_and_services",
    ],
    "Sports & Leisure": [
        "sports_leisure",
        "toys",
        "fashion_sport",
        "cool_stuff",
        "party_supplies",
        "christmas_supplies",
        "musical_instruments",
    ],
    "Fashion": [
        "watches_gifts",
        "fashion_bags_accessories",
        "fashion_shoes",
        "fashion_male_clothing",
        "fashio_female_clothing",
        "fashion_underwear_beach",
        "fashion_childrens_clothes",
        "luggage_accessories",
    ],
    "Books & Stationery": [
        "stationery",
        "books_general_interest",
        "books_technical",
        "books_imported",
        "art",
        "arts_and_craftmanship",
    ],
    "Baby & Pets": ["baby", "pet_shop"],
    "Auto & Industry": ["auto", "industry_commerce_and_business", "agro_industry_and_commerce", "market_place"],
    "Food & Drink": ["food_drink", "food", "drinks", "flowers"],
}
GROUP_OF = {c: g for g, cs in CATEGORY_GROUP.items() for c in cs}


def ensure_raw() -> bool:
    """Unzip data/raw/olist.zip (committed) into data/raw/olist/ when the CSVs are not there yet."""
    if (RAW / "olist_orders_dataset.csv").exists():
        return True
    archive = ROOT / "raw" / "olist.zip"
    if archive.exists():
        import zipfile

        RAW.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(RAW)
        return True
    return False


def main() -> int:
    if not ensure_raw():
        print(f"raw files missing: put olist.zip in {ROOT / 'raw'} or unzip the Kaggle download into {RAW}.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    ts_cols = [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ]
    orders = pd.read_csv(RAW / "olist_orders_dataset.csv", parse_dates=ts_cols)
    customers = pd.read_csv(RAW / "olist_customers_dataset.csv", dtype={"customer_zip_code_prefix": str})
    items = pd.read_csv(RAW / "olist_order_items_dataset.csv", parse_dates=["shipping_limit_date"])
    products = pd.read_csv(RAW / "olist_products_dataset.csv")
    translation = pd.read_csv(RAW / "product_category_name_translation.csv")
    translation.columns = [c.strip("﻿") for c in translation.columns]
    sellers = pd.read_csv(RAW / "olist_sellers_dataset.csv", dtype={"seller_zip_code_prefix": str})
    payments = pd.read_csv(RAW / "olist_order_payments_dataset.csv")
    reviews = pd.read_csv(
        RAW / "olist_order_reviews_dataset.csv", parse_dates=["review_creation_date", "review_answer_timestamp"]
    )
    geo = pd.read_csv(RAW / "olist_geolocation_dataset.csv", dtype={"geolocation_zip_code_prefix": str})

    # ---- dim_date: cover the whole order range plus the estimated-delivery horizon
    start, end = (
        orders.order_purchase_timestamp.min().normalize(),
        orders.order_estimated_delivery_date.max().normalize(),
    )
    days = pd.date_range(start, end, freq="D")
    dim_date = pd.DataFrame(
        {
            "date_key": days.date,
            "year": days.year,
            "quarter": days.quarter,
            "month": days.month,
            "month_name": days.strftime("%b"),
            "year_month": days.strftime("%Y-%m"),
            "year_quarter": [f"{d.year}-Q{d.quarter}" for d in days],
            "day_of_week": days.dayofweek + 1,
            "is_weekend": (days.dayofweek >= 5).astype(int),
        }
    )

    # ---- golden customer record (one per customer_unique_id; attributes from the latest order)
    oc = orders[["order_id", "customer_id", "order_purchase_timestamp"]].merge(customers, on="customer_id", how="left")
    oc = oc.sort_values("order_purchase_timestamp")
    latest = oc.groupby("customer_unique_id").tail(1).set_index("customer_unique_id")
    agg = oc.groupby("customer_unique_id").agg(
        first_order_date=("order_purchase_timestamp", "min"),
        last_order_date=("order_purchase_timestamp", "max"),
        order_count=("order_id", "nunique"),
        source_id_count=("customer_id", "nunique"),
    )
    dim_customer = agg.join(latest[["customer_state", "customer_city", "customer_zip_code_prefix"]]).reset_index()
    dim_customer = dim_customer.sort_values("first_order_date").reset_index(drop=True)
    dim_customer.insert(0, "customer_key", range(1, len(dim_customer) + 1))
    dim_customer["region"] = dim_customer.customer_state.map(REGION).fillna("Unknown")
    dim_customer["is_repeat"] = (dim_customer.order_count > 1).astype(int)
    dim_customer = dim_customer.rename(columns={"customer_zip_code_prefix": "zip_prefix"})
    dim_customer["first_order_date"] = dim_customer.first_order_date.dt.date
    dim_customer["last_order_date"] = dim_customer.last_order_date.dt.date
    dim_customer["customer_city"] = dim_customer.customer_city.str.title()
    dim_customer = dim_customer[
        [
            "customer_key",
            "customer_unique_id",
            "customer_state",
            "customer_city",
            "zip_prefix",
            "region",
            "first_order_date",
            "last_order_date",
            "order_count",
            "is_repeat",
            "source_id_count",
        ]
    ]
    key_of_unique = dim_customer.set_index("customer_unique_id").customer_key
    customer_key_of_order = oc.set_index("order_id").customer_unique_id.map(key_of_unique)

    # ---- dim_seller
    dim_seller = sellers.rename(columns={"seller_zip_code_prefix": "zip_prefix"}).copy()
    dim_seller.insert(0, "seller_key", range(1, len(dim_seller) + 1))
    dim_seller["seller_region"] = dim_seller.seller_state.map(REGION).fillna("Unknown")
    dim_seller["seller_city"] = dim_seller.seller_city.str.title()
    dim_seller = dim_seller[["seller_key", "seller_id", "seller_state", "seller_city", "seller_region"]]

    # ---- dim_product
    tr = dict(zip(translation.iloc[:, 0], translation.iloc[:, 1])) | EXTRA_TRANSLATIONS
    dim_product = products.copy()
    dim_product.insert(0, "product_key", range(1, len(dim_product) + 1))
    dim_product["category_pt"] = dim_product.product_category_name
    dim_product["category"] = dim_product.product_category_name.map(tr).fillna("unknown")
    dim_product["category_group"] = dim_product.category.map(GROUP_OF).fillna("Other")
    dim_product = dim_product.rename(
        columns={
            "product_weight_g": "weight_g",
            "product_length_cm": "length_cm",
            "product_height_cm": "height_cm",
            "product_width_cm": "width_cm",
            "product_photos_qty": "photos_qty",
            "product_name_lenght": "name_length",
            "product_description_lenght": "description_length",
        }
    )
    dim_product = dim_product[
        [
            "product_key",
            "product_id",
            "category",
            "category_pt",
            "category_group",
            "weight_g",
            "length_cm",
            "height_cm",
            "width_cm",
            "photos_qty",
            "name_length",
            "description_length",
        ]
    ]
    for c in ["weight_g", "length_cm", "height_cm", "width_cm", "photos_qty", "name_length", "description_length"]:
        dim_product[c] = dim_product[c].astype("Int64")

    # ---- payments (fact + per-order summary)
    fact_payments = payments.rename(
        columns={"payment_sequential": "payment_seq", "payment_installments": "installments"}
    )
    fact_payments = fact_payments[["order_id", "payment_seq", "payment_type", "installments", "payment_value"]]
    pay_main = (
        payments.sort_values(["order_id", "payment_value"], ascending=[True, False])
        .groupby("order_id")
        .head(1)
        .set_index("order_id")
        .payment_type
    )
    pay_sum = payments.groupby("order_id").agg(
        payment_value=("payment_value", "sum"), installments=("payment_installments", "max")
    )

    # ---- reviews (fact + latest per order)
    rv = reviews.sort_values(["order_id", "review_creation_date", "review_answer_timestamp"])
    fact_reviews = pd.DataFrame(
        {
            "review_id": rv.review_id,
            "order_id": rv.order_id,
            "review_score": rv.review_score.astype(int),
            "has_title": rv.review_comment_title.notna().astype(int),
            "has_message": rv.review_comment_message.notna().astype(int),
            "comment_title": rv.review_comment_title,
            "comment_message": rv.review_comment_message.str.replace(r"\s+", " ", regex=True),
            "review_date": rv.review_creation_date.dt.date,
            "answered_ts": rv.review_answer_timestamp,
        }
    ).drop_duplicates(["review_id", "order_id"])
    latest_review = rv.groupby("order_id").tail(1).set_index("order_id")

    # ---- items + order totals
    item_tot = items.groupby("order_id").agg(
        item_count=("order_item_id", "count"), product_value=("price", "sum"), freight_value=("freight_value", "sum")
    )

    # ---- fact_orders
    fo = orders.set_index("order_id")
    delivered = fo.order_delivered_customer_date.notna() & fo.order_status.eq("delivered")
    fact_orders = pd.DataFrame(
        {
            "order_id": fo.index,
            "customer_key": customer_key_of_order.reindex(fo.index).values,
            "purchase_date": fo.order_purchase_timestamp.dt.date.values,
            "purchase_ts": fo.order_purchase_timestamp.values,
            "status": fo.order_status.values,
            "approved_ts": fo.order_approved_at.values,
            "carrier_ts": fo.order_delivered_carrier_date.values,
            "delivered_ts": fo.order_delivered_customer_date.values,
            "estimated_delivery_date": fo.order_estimated_delivery_date.dt.date.values,
        }
    ).set_index("order_id")
    dd = (fo.order_delivered_customer_date - fo.order_purchase_timestamp).dt.total_seconds() / 86400
    late = (fo.order_delivered_customer_date.dt.normalize() - fo.order_estimated_delivery_date.dt.normalize()).dt.days
    fact_orders["delivery_days"] = dd.where(delivered).round(2)
    fact_orders["delivered_late"] = (late > 0).astype("float").where(delivered).astype("Int64")
    fact_orders["days_late"] = late.where(delivered).astype("float")
    fact_orders = fact_orders.join(item_tot).join(pay_sum)
    fact_orders["item_count"] = fact_orders.item_count.fillna(0).astype(int)
    fact_orders["product_value"] = fact_orders.product_value.fillna(0).round(2)
    fact_orders["freight_value"] = fact_orders.freight_value.fillna(0).round(2)
    fact_orders["payment_value"] = fact_orders.payment_value.round(2)
    fact_orders["installments"] = fact_orders.installments.astype("Int64")
    fact_orders["payment_type"] = pay_main.reindex(fact_orders.index)
    fact_orders["review_score"] = latest_review.review_score.reindex(fact_orders.index).astype("Int64")
    fact_orders["review_has_text"] = (
        latest_review.review_comment_message.notna().astype(int).reindex(fact_orders.index).astype("Int64")
    )
    fact_orders = fact_orders.reset_index()

    # ---- fact_order_items
    pk = dim_product.set_index("product_id").product_key
    sk = dim_seller.set_index("seller_id").seller_key
    fi = items.merge(fact_orders[["order_id", "customer_key", "purchase_date", "status"]], on="order_id", how="left")
    fact_order_items = pd.DataFrame(
        {
            "order_id": fi.order_id,
            "item_seq": fi.order_item_id,
            "product_key": fi.product_id.map(pk),
            "seller_key": fi.seller_id.map(sk),
            "customer_key": fi.customer_key,
            "purchase_date": fi.purchase_date,
            "status": fi.status,
            "price": fi.price,
            "freight_value": fi.freight_value,
            "shipping_limit_date": fi.shipping_limit_date.dt.date,
        }
    )

    # ---- dim_geo: one row per zip prefix
    g = geo[
        (geo.geolocation_lat.between(-34, 6)) & (geo.geolocation_lng.between(-74, -34))
    ]  # drop points outside Brazil
    dim_geo = (
        g.groupby("geolocation_zip_code_prefix")
        .agg(
            lat=("geolocation_lat", "mean"),
            lng=("geolocation_lng", "mean"),
            city=("geolocation_city", lambda s: s.mode().iat[0]),
            state=("geolocation_state", lambda s: s.mode().iat[0]),
            point_count=("geolocation_lat", "size"),
        )
        .reset_index()
        .rename(columns={"geolocation_zip_code_prefix": "zip_prefix"})
    )
    dim_geo["city"] = dim_geo.city.str.title()
    dim_geo["lat"] = dim_geo.lat.round(5)
    dim_geo["lng"] = dim_geo.lng.round(5)

    for name, df in {
        "dim_date": dim_date,
        "dim_customer": dim_customer,
        "dim_seller": dim_seller,
        "dim_product": dim_product,
        "dim_geo": dim_geo,
        "fact_orders": fact_orders,
        "fact_order_items": fact_order_items,
        "fact_payments": fact_payments,
        "fact_reviews": fact_reviews,
    }.items():
        df.to_csv(OUT / f"{name}.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
        print(f"{name:18s} {len(df):>8,d} rows")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
