"""Deterministic per-serving nutrition from ingredients.

Values are rounded USDA FoodData Central / SR Legacy figures per 100 g (public
data). Optional USDA FDC search is used only when a key is set in Settings
(usda_fdc_api_key) or USDA_FDC_API_KEY in the environment — the key is never
hardcoded.
"""

from __future__ import annotations

import os
from typing import Any

from helpers import extract_servings, setup_logger
from recipe_schema import sanitize_nutrition_fields
from units import quantity_to_grams

logger = setup_logger(__name__)

# (kcal, protein_g, fat_g, carb_g, fiber_g, sugar_g, sodium_mg, cholesterol_mg)
_N = tuple[float, float, float, float, float, float, float, float]

_FOODS: dict[str, dict[str, Any]] = {
    "flour": {"n": (364, 10.3, 1.0, 76.3, 2.7, 0.3, 2, 0), "density": 0.53, "aliases": ["all-purpose flour", "white flour", "קמח", "קמח לבן"]},
    "sugar": {"n": (387, 0, 0, 100, 0, 99.8, 1, 0), "density": 0.85, "aliases": ["white sugar", "granulated sugar", "סוכר"]},
    "brown sugar": {"n": (380, 0.1, 0, 98, 0, 97, 28, 0), "aliases": ["סוכר חום"]},
    "butter": {"n": (717, 0.9, 81, 0.1, 0, 0.1, 11, 215), "density": 0.91, "aliases": ["חמאה"]},
    "olive oil": {"n": (884, 0, 100, 0, 0, 0, 2, 0), "density": 0.91, "aliases": ["extra virgin olive oil", "שמן זית"]},
    "vegetable oil": {"n": (884, 0, 100, 0, 0, 0, 0, 0), "density": 0.92, "aliases": ["canola oil", "שמן"]},
    "egg": {"n": (143, 12.6, 9.5, 0.7, 0, 0.4, 142, 372), "piece_g": 50, "aliases": ["eggs", "ביצה", "ביצים"]},
    "milk": {"n": (61, 3.2, 3.3, 4.8, 0, 5.1, 43, 10), "density": 1.03, "aliases": ["whole milk", "חלב"]},
    "yogurt": {"n": (61, 3.5, 3.3, 4.7, 0, 4.7, 46, 13), "aliases": ["יוגורט"]},
    "cream": {"n": (340, 2.1, 36, 2.8, 0, 2.8, 38, 110), "density": 0.99, "aliases": ["heavy cream", "שמנת"]},
    "cheese": {"n": (402, 25, 33, 1.3, 0, 0.5, 621, 105), "aliases": ["cheddar", "גבינה"]},
    "parmesan": {"n": (431, 38, 29, 4.1, 0, 0.9, 1529, 88), "aliases": ["פרמזן"]},
    "chicken breast": {"n": (165, 31, 3.6, 0, 0, 0, 74, 85), "aliases": ["chicken", "חזה עוף", "עוף"]},
    "chicken thigh": {"n": (177, 24, 8.2, 0, 0, 0, 84, 93), "aliases": ["ירך עוף"]},
    "beef": {"n": (250, 26, 15, 0, 0, 0, 72, 90), "aliases": ["ground beef", "בקר", "בשר"]},
    "pork": {"n": (242, 27, 14, 0, 0, 0, 62, 80), "aliases": ["חזיר"]},
    "salmon": {"n": (208, 20, 13, 0, 0, 0, 59, 55), "aliases": ["סלמון"]},
    "tuna": {"n": (132, 28, 1.3, 0, 0, 0, 47, 47), "aliases": ["טונה"]},
    "shrimp": {"n": (99, 24, 0.3, 0.2, 0, 0, 111, 189), "aliases": ["prawn", "שרימפס"]},
    "tofu": {"n": (76, 8, 4.8, 1.9, 0.3, 0.6, 7, 0), "aliases": ["טופו"]},
    "rice": {"n": (130, 2.7, 0.3, 28, 0.4, 0.1, 1, 0), "aliases": ["white rice", "אורז"]},
    "pasta": {"n": (131, 5, 1.1, 25, 1.8, 0.6, 1, 0), "aliases": ["spaghetti", "noodle", "noodles", "פסטה", "ספגטי"]},
    "bread": {"n": (265, 9, 3.2, 49, 2.7, 5, 491, 0), "piece_g": 30, "aliases": ["לחם"]},
    "potato": {"n": (77, 2, 0.1, 17, 2.2, 0.8, 6, 0), "piece_g": 170, "aliases": ["potatoes", "תפוח אדמה", "תפוחי אדמה"]},
    "onion": {"n": (40, 1.1, 0.1, 9.3, 1.7, 4.2, 4, 0), "piece_g": 110, "aliases": ["onions", "בצל"]},
    "garlic": {"n": (149, 6.4, 0.5, 33, 2.1, 1, 17, 0), "piece_g": 5, "aliases": ["garlic clove", "שום"]},
    "tomato": {"n": (18, 0.9, 0.2, 3.9, 1.2, 2.6, 5, 0), "piece_g": 120, "aliases": ["tomatoes", "עגבניה", "עגבניות"]},
    "tomato paste": {"n": (82, 4.3, 0.5, 19, 4.1, 12, 59, 0), "aliases": ["רסק עגבניות"]},
    "carrot": {"n": (41, 0.9, 0.2, 10, 2.8, 4.7, 69, 0), "piece_g": 60, "aliases": ["carrots", "גזר"]},
    "celery": {"n": (16, 0.7, 0.2, 3, 1.6, 1.3, 80, 0), "aliases": ["סלרי"]},
    "bell pepper": {"n": (31, 1, 0.3, 6, 2.1, 4.2, 4, 0), "aliases": ["pepper", "red pepper", "פלפל"]},
    "chili": {"n": (40, 1.9, 0.4, 8.8, 1.5, 5.3, 9, 0), "aliases": ["chili pepper", "צ'ילי", "צ׳ילי"]},
    "cucumber": {"n": (15, 0.7, 0.1, 3.6, 0.5, 1.7, 2, 0), "aliases": ["מלפפון"]},
    "lettuce": {"n": (15, 1.4, 0.2, 2.9, 1.3, 0.8, 28, 0), "aliases": ["חסה"]},
    "spinach": {"n": (23, 2.9, 0.4, 3.6, 2.2, 0.4, 79, 0), "aliases": ["תרד"]},
    "broccoli": {"n": (34, 2.8, 0.4, 7, 2.6, 1.7, 33, 0), "aliases": ["ברוקולי"]},
    "mushroom": {"n": (22, 3.1, 0.3, 3.3, 1, 2, 5, 0), "aliases": ["mushrooms", "פטריות"]},
    "avocado": {"n": (160, 2, 15, 8.5, 6.7, 0.7, 7, 0), "piece_g": 150, "aliases": ["אבוקדו"]},
    "lemon": {"n": (29, 1.1, 0.3, 9, 2.8, 2.5, 2, 0), "piece_g": 60, "aliases": ["lemon juice", "לימון"]},
    "lime": {"n": (30, 0.7, 0.2, 11, 2.8, 1.7, 2, 0), "aliases": ["ליים"]},
    "apple": {"n": (52, 0.3, 0.2, 14, 2.4, 10, 1, 0), "piece_g": 180, "aliases": ["תפוח"]},
    "banana": {"n": (89, 1.1, 0.3, 23, 2.6, 12, 1, 0), "piece_g": 120, "aliases": ["בננה"]},
    "strawberry": {"n": (32, 0.7, 0.3, 7.7, 2, 4.9, 1, 0), "aliases": ["strawberries", "תות"]},
    "salt": {"n": (0, 0, 0, 0, 0, 0, 38758, 0), "aliases": ["kosher salt", "sea salt", "מלח"]},
    "black pepper": {"n": (251, 10, 3.3, 64, 25, 0.6, 20, 0), "aliases": ["pepper", "פלפל שחור"]},
    "soy sauce": {"n": (53, 8.1, 0.1, 4.9, 0.8, 0.4, 5493, 0), "density": 1.2, "aliases": ["סויו", "רוטב סויה"]},
    "vinegar": {"n": (18, 0, 0, 0.04, 0, 0.04, 2, 0), "density": 1.01, "aliases": ["חומץ"]},
    "honey": {"n": (304, 0.3, 0, 82, 0.2, 82, 4, 0), "density": 1.42, "aliases": ["דבש"]},
    "maple syrup": {"n": (260, 0, 0.1, 67, 0, 60, 12, 0), "density": 1.32, "aliases": ["סירופ מייפל"]},
    "cocoa": {"n": (228, 20, 14, 58, 37, 1.8, 21, 0), "aliases": ["cocoa powder", "קקאו"]},
    "chocolate": {"n": (546, 4.9, 31, 61, 7, 48, 24, 8), "aliases": ["שוקולד"]},
    "almond": {"n": (579, 21, 50, 22, 12.5, 4.4, 1, 0), "aliases": ["almonds", "שקד", "שקדים"]},
    "walnut": {"n": (654, 15, 65, 14, 6.7, 2.6, 2, 0), "aliases": ["walnuts", "אגוז מלך"]},
    "peanut": {"n": (567, 26, 49, 16, 8.5, 4.7, 18, 0), "aliases": ["peanuts", "בוטן", "בוטנים"]},
    "sesame": {"n": (573, 18, 50, 23, 12, 0.3, 11, 0), "aliases": ["sesame seeds", "שומשום"]},
    "tahini": {"n": (595, 17, 54, 21, 9.3, 0.5, 35, 0), "density": 1.1, "aliases": ["טחינה"]},
    "chickpea": {"n": (164, 8.9, 2.6, 27, 7.6, 4.8, 7, 0), "aliases": ["chickpeas", "garbanzo", "גרגרים", "חומוס"]},
    "lentil": {"n": (116, 9, 0.4, 20, 7.9, 1.8, 2, 0), "aliases": ["lentils", "עדשים"]},
    "bean": {"n": (127, 8.7, 0.5, 23, 6.4, 0.3, 2, 0), "aliases": ["beans", "black beans", "שעועית"]},
    "corn": {"n": (86, 3.3, 1.4, 19, 2, 3.2, 15, 0), "aliases": ["תירס"]},
    "oat": {"n": (389, 17, 6.9, 66, 10.6, 0.9, 2, 0), "aliases": ["oats", "oatmeal", "שיבולת שועל"]},
    "coconut milk": {"n": (197, 2, 21, 3, 2.2, 3.3, 13, 0), "density": 1.0, "aliases": ["חלב קוקוס"]},
    "ginger": {"n": (80, 1.8, 0.8, 18, 2, 1.7, 13, 0), "aliases": ["ג'ינג'ר", "ג׳ינג׳ר", "זנגביל"]},
    "basil": {"n": (23, 3.2, 0.6, 2.7, 1.6, 0.3, 4, 0), "aliases": ["בזיליקום"]},
    "parsley": {"n": (36, 3, 0.8, 6.3, 3.3, 0.9, 56, 0), "aliases": ["פטרוזיליה"]},
    "cilantro": {"n": (23, 2.1, 0.5, 3.7, 2.8, 0.9, 46, 0), "aliases": ["coriander", "כוסברה"]},
    "water": {"n": (0, 0, 0, 0, 0, 0, 0, 0), "density": 1.0, "aliases": ["מים"]},
    "yeast": {"n": (325, 40, 8, 41, 27, 0, 51, 0), "aliases": ["instant yeast", "dry yeast", "שמרים"]},
    "baking powder": {"n": (53, 0, 0, 28, 0, 0, 8526, 0), "aliases": ["אבקת אפייה"]},
    "baking soda": {"n": (0, 0, 0, 0, 0, 0, 27360, 0), "aliases": ["sodium bicarbonate", "סודה לשתייה"]},
    "cornstarch": {"n": (381, 0.3, 0.1, 91, 0.9, 0, 9, 0), "aliases": ["corn starch", "cornflour", "עמילן", "קורנפלור"]},
    "potato starch": {"n": (357, 0, 0.1, 83, 0, 0, 55, 0), "aliases": ["עמילן תפוחי אדמה"]},
    "breadcrumbs": {"n": (395, 13, 5.3, 72, 4.5, 6.2, 732, 0), "aliases": ["panko", "פירורי לחם"]},
    "pita": {"n": (275, 9.1, 1.2, 56, 2.2, 1.3, 536, 0), "piece_g": 60, "aliases": ["pita bread", "פיתה", "פיתות"]},
    "tortilla": {"n": (312, 8.2, 7.1, 52, 3.1, 1.2, 472, 0), "aliases": ["טורטיה"]},
    "couscous": {"n": (112, 3.8, 0.2, 23, 1.4, 0.1, 5, 0), "aliases": ["קוסקוס"]},
    "quinoa": {"n": (120, 4.4, 1.9, 21, 2.8, 0.9, 7, 0), "aliases": ["קינואה"]},
    "barley": {"n": (123, 2.3, 0.4, 28, 3.8, 0.8, 3, 0), "aliases": ["pearl barley", "שעורה"]},
    "bulgur": {"n": (83, 3.1, 0.2, 19, 4.5, 0.1, 5, 0), "aliases": ["בורגול"]},
    "semolina": {"n": (360, 13, 1.1, 73, 3.9, 0, 1, 0), "aliases": ["סולת"]},
    "polenta": {"n": (85, 1.9, 0.5, 18, 1.1, 0.2, 1, 0), "aliases": ["cornmeal", "פולנטה"]},
    "phyllo": {"n": (299, 7.1, 6, 53, 1.9, 0.2, 483, 0), "aliases": ["filo", "פילו"]},
    "lamb": {"n": (294, 25, 21, 0, 0, 0, 72, 97), "aliases": ["כבש", "טלה"]},
    "turkey": {"n": (189, 29, 7.4, 0, 0, 0, 70, 82), "aliases": ["הודו"]},
    "duck": {"n": (337, 19, 28, 0, 0, 0, 59, 84), "aliases": ["ברווז"]},
    "veal": {"n": (172, 24, 8, 0, 0, 0, 83, 88), "aliases": ["עגל"]},
    "bacon": {"n": (541, 37, 42, 1.4, 0, 0, 1717, 110), "aliases": ["בייקון"]},
    "sausage": {"n": (301, 12, 27, 2.7, 0, 0.9, 721, 61), "aliases": ["נקניק", "נקניקיות"]},
    "cod": {"n": (82, 18, 0.7, 0, 0, 0, 54, 43), "aliases": ["white fish", "בקלה", "דג לבן"]},
    "tilapia": {"n": (96, 20, 1.7, 0, 0, 0, 52, 50), "aliases": ["אמנון"]},
    "sardine": {"n": (208, 25, 11, 0, 0, 0, 307, 142), "aliases": ["sardines", "סרדינים"]},
    "anchovy": {"n": (131, 20, 4.8, 0, 0, 0, 3668, 60), "aliases": ["anchovies", "אנשובי"]},
    "crab": {"n": (87, 18, 1.1, 0, 0, 0, 293, 78), "aliases": ["סרטן"]},
    "squid": {"n": (92, 16, 1.4, 3.1, 0, 0, 44, 233), "aliases": ["calamari", "דיונון"]},
    "feta": {"n": (264, 14, 21, 4.1, 0, 4.1, 1116, 89), "aliases": ["feta cheese", "פטה", "בולגרית"]},
    "mozzarella": {"n": (280, 28, 17, 3.1, 0, 1.2, 627, 79), "aliases": ["מוצרלה"]},
    "ricotta": {"n": (174, 11, 13, 3, 0, 0.3, 84, 51), "aliases": ["ריקוטה"]},
    "cottage cheese": {"n": (98, 11, 4.3, 3.4, 0, 2.7, 364, 17), "aliases": ["cottage", "קוטג'"]},
    "cream cheese": {"n": (342, 6.2, 34, 4.1, 0, 3.2, 321, 110), "aliases": ["גבינת שמנת"]},
    "goat cheese": {"n": (364, 22, 30, 0.1, 0, 0.1, 515, 79), "aliases": ["גבינת עזים"]},
    "labneh": {"n": (146, 8, 11, 4, 0, 4, 50, 35), "aliases": ["labane", "לאבנה"]},
    "white cheese": {"n": (96, 11, 4.5, 2.5, 0, 2.5, 350, 15), "aliases": ["gvina levana", "גבינה לבנה"]},
    "halloumi": {"n": (321, 21, 25, 2.2, 0, 2.2, 1100, 70), "aliases": ["חלומי"]},
    "sour cream": {"n": (198, 2.4, 19, 4.6, 0, 3.4, 31, 59), "density": 0.98, "aliases": ["שמנת חמוצה"]},
    "buttermilk": {"n": (40, 3.3, 0.9, 4.8, 0, 4.8, 105, 4), "density": 1.03, "aliases": ["חלב חמוץ"]},
    "mayonnaise": {"n": (680, 1, 75, 0.6, 0, 0.6, 635, 42), "density": 0.91, "aliases": ["מיונז"]},
    "mustard": {"n": (60, 3.7, 3.3, 5.8, 3.3, 0.9, 1135, 0), "aliases": ["חרדל"]},
    "ketchup": {"n": (112, 1.3, 0.2, 27, 0.3, 22, 907, 0), "density": 1.15, "aliases": ["קטשופ"]},
    "tomato sauce": {"n": (29, 1.3, 0.2, 7, 1.5, 4.5, 431, 0), "aliases": ["רוטב עגבניות", "רוטב נפוליטנה"]},
    "chicken stock": {"n": (5, 0.6, 0.2, 0.4, 0, 0.2, 343, 1), "density": 1.01, "aliases": ["chicken broth", "ציר עוף", "מרק עוף"]},
    "beef stock": {"n": (7, 1.1, 0.2, 0.1, 0, 0, 372, 1), "aliases": ["beef broth", "ציר בקר"]},
    "coconut oil": {"n": (862, 0, 100, 0, 0, 0, 0, 0), "density": 0.92, "aliases": ["שמן קוקוס"]},
    "sesame oil": {"n": (884, 0, 100, 0, 0, 0, 0, 0), "density": 0.92, "aliases": ["שמן שומשום"]},
    "margarine": {"n": (717, 0.2, 81, 0.7, 0, 0, 670, 0), "density": 0.91, "aliases": ["מרגרינה"]},
    "eggplant": {"n": (25, 1, 0.2, 6, 3, 3.5, 2, 0), "piece_g": 400, "aliases": ["aubergine", "חציל", "חצילים"]},
    "zucchini": {"n": (17, 1.2, 0.3, 3.1, 1, 2.5, 8, 0), "piece_g": 200, "aliases": ["courgette", "קישוא", "קישואים"]},
    "cauliflower": {"n": (25, 1.9, 0.3, 5, 2, 1.9, 30, 0), "aliases": ["כרובית"]},
    "cabbage": {"n": (25, 1.3, 0.1, 6, 2.5, 3.2, 18, 0), "aliases": ["כרוב"]},
    "red cabbage": {"n": (31, 1.4, 0.2, 7.4, 2.1, 3.8, 27, 0), "aliases": ["כרוב אדום"]},
    "sweet potato": {"n": (86, 1.6, 0.1, 20, 3, 4.2, 55, 0), "piece_g": 130, "aliases": ["בטטה"]},
    "pumpkin": {"n": (26, 1, 0.1, 7, 0.5, 2.8, 1, 0), "aliases": ["דלעת"]},
    "squash": {"n": (40, 1, 0.1, 10, 1.5, 2.2, 4, 0), "aliases": ["דלורית"]},
    "peas": {"n": (81, 5.4, 0.4, 14, 5.1, 5.7, 5, 0), "aliases": ["green peas", "אפונה"]},
    "green bean": {"n": (31, 1.8, 0.2, 7, 2.7, 3.3, 6, 0), "aliases": ["green beans", "שעועית ירוקה"]},
    "kale": {"n": (49, 4.3, 0.9, 9, 3.6, 2.3, 38, 0), "aliases": ["קייל"]},
    "asparagus": {"n": (20, 2.2, 0.1, 3.9, 2.1, 1.9, 2, 0), "aliases": ["אספרגוס"]},
    "artichoke": {"n": (47, 3.3, 0.2, 11, 5.4, 1, 94, 0), "aliases": ["ארטישוק"]},
    "leek": {"n": (61, 1.5, 0.3, 14, 1.8, 3.9, 20, 0), "aliases": ["כרישה"]},
    "shallot": {"n": (72, 2.5, 0.1, 17, 3.2, 7.9, 12, 0), "aliases": ["שאלוט"]},
    "scallion": {"n": (32, 1.8, 0.2, 7.3, 2.6, 2.3, 16, 0), "aliases": ["green onion", "spring onion", "בצל ירוק"]},
    "beet": {"n": (43, 1.6, 0.2, 10, 2.8, 6.8, 78, 0), "aliases": ["beetroot", "סלק"]},
    "radish": {"n": (16, 0.7, 0.1, 3.4, 1.6, 1.9, 39, 0), "aliases": ["צנון"]},
    "fennel": {"n": (31, 1.2, 0.2, 7.3, 3.1, 3.9, 52, 0), "aliases": ["שומר"]},
    "olive": {"n": (115, 0.8, 11, 6, 3.2, 0, 735, 0), "aliases": ["olives", "זית", "זיתים"]},
    "capers": {"n": (23, 2.4, 0.9, 4.9, 3.2, 0.4, 2348, 0), "aliases": ["צלפים"]},
    "pickle": {"n": (11, 0.3, 0.2, 2.3, 1.2, 1.1, 1208, 0), "aliases": ["pickles", "מלפפון חמוץ"]},
    "orange": {"n": (47, 0.9, 0.1, 12, 2.4, 9.4, 0, 0), "piece_g": 130, "aliases": ["תפוז"]},
    "date": {"n": (282, 2.5, 0.4, 75, 8, 63, 2, 0), "aliases": ["dates", "תמר", "תמרים"]},
    "raisin": {"n": (299, 3.1, 0.5, 79, 3.7, 59, 11, 0), "aliases": ["raisins", "צימוקים"]},
    "pomegranate": {"n": (83, 1.7, 1.2, 19, 4, 14, 3, 0), "aliases": ["רימון"]},
    "grape": {"n": (69, 0.7, 0.2, 18, 0.9, 16, 2, 0), "aliases": ["grapes", "ענבים"]},
    "mango": {"n": (60, 0.8, 0.4, 15, 1.6, 14, 1, 0), "aliases": ["מנגו"]},
    "pineapple": {"n": (50, 0.5, 0.1, 13, 1.4, 10, 1, 0), "aliases": ["אננס"]},
    "coconut": {"n": (354, 3.3, 33, 15, 9, 6.2, 20, 0), "aliases": ["קוקוס"]},
    "pecan": {"n": (691, 9.2, 72, 14, 9.6, 4, 0, 0), "aliases": ["pecans", "פקאן"]},
    "cashew": {"n": (553, 18, 44, 30, 3.3, 5.9, 12, 0), "aliases": ["cashews", "קשיו"]},
    "pistachio": {"n": (560, 20, 45, 27, 10, 7.7, 1, 0), "aliases": ["pistachios", "פיסטוק"]},
    "pine nut": {"n": (673, 14, 68, 13, 3.7, 3.6, 2, 0), "aliases": ["pine nuts", "צנובר"]},
    "sunflower seed": {"n": (584, 21, 51, 20, 8.6, 2.6, 9, 0), "aliases": ["sunflower seeds", "גרעיני חמנייה"]},
    "pumpkin seed": {"n": (559, 30, 49, 11, 6, 1.4, 7, 0), "aliases": ["pumpkin seeds", "גרעיני דלעת"]},
    "chia": {"n": (486, 17, 31, 42, 34, 0, 16, 0), "aliases": ["chia seeds", "צ'יה"]},
    "flax": {"n": (534, 18, 42, 29, 27, 1.6, 30, 0), "aliases": ["flaxseed", "flax seeds", "פשתן"]},
    "peanut butter": {"n": (588, 25, 50, 20, 6, 9.2, 17, 0), "density": 1.1, "aliases": ["חמאת בוטנים"]},
    "almond butter": {"n": (614, 21, 56, 19, 10, 4.4, 7, 0), "aliases": ["חמאת שקדים"]},
    "powdered sugar": {"n": (389, 0, 0, 100, 0, 100, 1, 0), "aliases": ["icing sugar", "אבקת סוכר"]},
    "molasses": {"n": (290, 0, 0.1, 75, 0, 55, 37, 0), "density": 1.4, "aliases": ["סילאן", "דבש תמרים"]},
    "date syrup": {"n": (290, 0.5, 0, 75, 0, 70, 20, 0), "density": 1.4, "aliases": ["silan"]},
    "jam": {"n": (278, 0.4, 0.1, 69, 1.1, 49, 32, 0), "aliases": ["jelly", "marmalade", "ריבה"]},
    "vanilla": {"n": (288, 0.1, 0.1, 13, 0, 13, 9, 0), "aliases": ["vanilla extract", "וניל"]},
    "cinnamon": {"n": (247, 4, 1.2, 81, 53, 2.2, 10, 0), "aliases": ["קינמון"]},
    "nutmeg": {"n": (525, 6, 36, 49, 21, 3, 16, 0), "aliases": ["אגוז מוסקט"]},
    "paprika": {"n": (282, 14, 13, 54, 35, 10, 68, 0), "aliases": ["פפריקה"]},
    "cumin": {"n": (375, 18, 22, 44, 11, 2.3, 168, 0), "aliases": ["כמון"]},
    "turmeric": {"n": (312, 9.7, 3.3, 67, 23, 3.2, 38, 0), "aliases": ["כורכום"]},
    "oregano": {"n": (265, 9, 4.3, 69, 43, 4.1, 25, 0), "aliases": ["אורגנו"]},
    "thyme": {"n": (101, 5.6, 1.7, 24, 14, 0, 9, 0), "aliases": ["טימין"]},
    "rosemary": {"n": (131, 3.3, 5.9, 21, 14, 0, 26, 0), "aliases": ["רוזמרין"]},
    "dill": {"n": (43, 3.5, 1.1, 7, 2.1, 0, 61, 0), "aliases": ["שמיר"]},
    "mint": {"n": (44, 3.3, 0.7, 8.4, 6.8, 0, 30, 0), "aliases": ["נענע"]},
    "zaatar": {"n": (284, 9, 7, 52, 20, 1, 400, 0), "aliases": ["za'atar", "זעתר"]},
    "sumac": {"n": (250, 5, 15, 26, 20, 0, 10, 0), "aliases": ["סומאק"]},
    "harissa": {"n": (128, 2.5, 9, 10, 3, 4, 680, 0), "aliases": ["חריסה"]},
    "pomegranate molasses": {"n": (270, 0.5, 0, 67, 0.5, 60, 15, 0), "density": 1.3, "aliases": ["דבש רימונים"]},
    "wine": {"n": (85, 0.1, 0, 2.6, 0, 0.6, 5, 0), "density": 0.99, "aliases": ["white wine", "red wine", "יין"]},
    "beer": {"n": (43, 0.5, 0, 3.6, 0, 0, 4, 0), "density": 1.01, "aliases": ["בירה"]},
    "rum": {"n": (231, 0, 0, 0, 0, 0, 1, 0), "aliases": ["רום"]},
    "soy milk": {"n": (33, 2.9, 1.8, 1.8, 0.6, 1, 51, 0), "density": 1.03, "aliases": ["חלב סויה"]},
    "almond milk": {"n": (15, 0.4, 1.1, 0.6, 0.2, 0, 63, 0), "density": 1.01, "aliases": ["חלב שקדים"]},
    "oat milk": {"n": (47, 1, 1.5, 7.5, 0.8, 4, 47, 0), "density": 1.03, "aliases": ["חלב שיבולת שועל"]},
    "condensed milk": {"n": (321, 7.9, 8.7, 54, 0, 54, 127, 34), "density": 1.3, "aliases": ["חלב מרוכז"]},
    "miso": {"n": (198, 12, 6, 26, 5.4, 6.2, 3728, 0), "aliases": ["מיסו"]},
    "fish sauce": {"n": (35, 5.1, 0, 3.6, 0, 3.6, 7851, 0), "density": 1.2, "aliases": ["רוטב דגים"]},
    "hot sauce": {"n": (11, 0.5, 0.4, 1.3, 0.3, 0.8, 2643, 0), "aliases": ["sriracha", "tabasco", "סרירצ'ה"]},
    "worcestershire": {"n": (78, 0, 0, 19, 0, 10, 980, 0), "aliases": ["worcestershire sauce"]},
    "kidney bean": {"n": (127, 8.7, 0.5, 23, 6.4, 0.3, 2, 0), "aliases": ["kidney beans", "שעועית אדומה"]},
    "white bean": {"n": (139, 9.7, 0.4, 25, 6.3, 0.3, 2, 0), "aliases": ["cannellini", "שעועית לבנה"]},
    "fava bean": {"n": (110, 7.6, 0.4, 19, 5.4, 1.8, 5, 0), "aliases": ["fava", "broad bean", "פול"]},
    "edamame": {"n": (121, 12, 5.2, 9, 5.2, 2.2, 6, 0), "aliases": ["אדממה", "סויה ירוקה"]},
}

_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _meta in _FOODS.items():
    _ALIAS_TO_CANON[_canon] = _canon
    for _alias in _meta.get("aliases") or []:
        _ALIAS_TO_CANON[_alias.casefold()] = _canon

_USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"


def _fold_food(name: str) -> str:
    return " ".join((name or "").casefold().split())


def match_food(name: str) -> str | None:
    """Return the canonical food key for an ingredient name, if any."""
    folded = _fold_food(name)
    if not folded:
        return None
    if folded in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[folded]
    for alias, canon in _ALIAS_TO_CANON.items():
        if alias and alias in folded:
            return canon
    return None


def _scale(n: _N, grams: float) -> _N:
    factor = grams / 100.0
    return tuple(v * factor for v in n)  # type: ignore[return-value]


def _add(a: _N, b: _N) -> _N:
    return tuple(x + y for x, y in zip(a, b))  # type: ignore[return-value]


def _zero() -> _N:
    return (0, 0, 0, 0, 0, 0, 0, 0)


def _usda_api_key() -> str:
    """Settings key first, then USDA_FDC_API_KEY in the environment. Never logged."""
    try:
        from config import config
        return (config.USDA_FDC_API_KEY or "").strip()
    except Exception:
        return (os.environ.get("USDA_FDC_API_KEY") or "").strip()


def _search_usda(food: str) -> _N | None:
    """Best-effort FDC lookup. Swallows errors; never logs the API key."""
    key = _usda_api_key()
    if not key:
        return None
    try:
        from helpers import create_http_session
        from url_safety import safe_get

        session = create_http_session()
        resp = safe_get(
            session,
            _USDA_SEARCH,
            params={"query": food, "pageSize": 1, "api_key": key},
            timeout=8,
        )
        resp.raise_for_status()
        foods = (resp.json() or {}).get("foods") or []
        if not foods:
            return None
        by_id = {
            int(n.get("nutrientId") or 0): float(n.get("value") or 0)
            for n in foods[0].get("foodNutrients") or []
            if n.get("nutrientId")
        }
        return (
            by_id.get(1008, 0),
            by_id.get(1003, 0),
            by_id.get(1004, 0),
            by_id.get(1005, 0),
            by_id.get(1079, 0),
            by_id.get(2000, 0),
            by_id.get(1093, 0),
            by_id.get(1253, 0),
        )
    except Exception as exc:
        logger.info("[Nutrition] USDA lookup skipped for %r: %s", food, type(exc).__name__)
        return None


def _nutrients_for(name: str) -> tuple[str, dict[str, Any]] | None:
    canon = match_food(name)
    if canon:
        return canon, _FOODS[canon]
    remote = _search_usda(name)
    if remote is None:
        return None
    return name, {"n": remote, "density": 1.0}


def ingredient_grams(item: dict) -> float | None:
    food = str(item.get("food") or "")
    meta = _FOODS.get(match_food(food) or "")
    density = float((meta or {}).get("density") or 1.0)
    piece_g = (meta or {}).get("piece_g")
    return quantity_to_grams(
        str(item.get("quantity") or ""),
        str(item.get("unit") or ""),
        density_g_per_ml=density,
        piece_grams=float(piece_g) if piece_g else None,
    )


def lookup_recipe_nutrition(recipe: dict) -> dict | None:
    """Return Schema.org NutritionInformation per serving, or None if too little matched."""
    ingredients = recipe.get("recipeIngredients") or []
    if not ingredients:
        return None
    total = _zero()
    matched = 0
    weighed = 0
    for item in ingredients:
        if not isinstance(item, dict):
            continue
        food = str(item.get("food") or "").strip()
        if not food:
            continue
        resolved = _nutrients_for(food)
        grams = ingredient_grams(item)
        if grams is None or grams <= 0 or resolved is None:
            continue
        _canon, meta = resolved
        total = _add(total, _scale(meta["n"], grams))
        matched += 1
        weighed += grams

    usable = sum(1 for i in ingredients if isinstance(i, dict) and str(i.get("food") or "").strip())
    if matched == 0 or weighed <= 0:
        return None
    if usable and matched / usable < 0.5:
        logger.info(
            "[Nutrition] local/USDA match rate %.0f%% — leaving nutrition to the LLM",
            100 * matched / usable,
        )
        return None

    servings = max(extract_servings(recipe), 1)
    kcal, protein, fat, carb, fiber, sugar, sodium, chol = (v / servings for v in total)
    raw = {
        "calories": f"{round(kcal)} kcal",
        "proteinContent": f"{round(protein, 1)} g",
        "fatContent": f"{round(fat, 1)} g",
        "carbohydrateContent": f"{round(carb, 1)} g",
        "fiberContent": f"{round(fiber, 1)} g",
        "sugarContent": f"{round(sugar, 1)} g",
        "sodiumContent": f"{round(sodium)} mg",
        "cholesterolContent": f"{round(chol)} mg",
    }
    cleaned = sanitize_nutrition_fields(raw)
    if not any(k != "@type" for k in cleaned):
        return None
    logger.info("[Nutrition] estimated from %d/%d ingredients, %d servings", matched, usable, servings)
    return cleaned
