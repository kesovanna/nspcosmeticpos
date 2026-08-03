import firebase_admin
from firebase_admin import credentials, firestore

# 1. Connect to your Firebase (Make sure your serviceAccountKey.json is in the same folder)
# Note: If you already initialized Firebase in another file, you might just need to import db.
try:
    cred = credentials.Certificate("serviceAccountKey.json") # Update this path if needed
    firebase_admin.initialize_app(cred)
except ValueError:
    pass # App already initialized

db = firestore.client()

# 2. The Comprehensive Cambodian Cosmetics Catalog
cosmetic_products = [
    # --- SKIN CARE ---
    {"name": "Hada Labo Gokujyun Cleansing Foam (ហ្វូមលាងមុខ)", "price": 8.00, "category": "cleanser", "barcode": "880600000001", "image": "hadalabo_foam.jpg"},
    {"name": "COSRX Low pH Good Morning Gel (ជែលលាងមុខ)", "price": 11.50, "category": "cleanser", "barcode": "880600000002", "image": "cosrx_cleanser.jpg"},
    {"name": "Cetaphil Gentle Skin Cleanser (សាប៊ូលាងមុខ)", "price": 15.00, "category": "cleanser", "barcode": "880600000003", "image": "cetaphil_cleanser.jpg"},
    {"name": "Pixi Glow Tonic Exfoliating Toner", "price": 18.00, "category": "toner", "barcode": "880600000004", "image": "pixi_glow_tonic.jpg"},
    {"name": "Thayers Witch Hazel Toner (ទឹកជូតមុខ)", "price": 12.00, "category": "toner", "barcode": "880600000005", "image": "thayers_toner.jpg"},
    {"name": "The Ordinary Niacinamide 10% + Zinc 1%", "price": 9.50, "category": "serum", "barcode": "880600000006", "image": "ordinary_niacinamide.jpg"},
    {"name": "Estée Lauder Advanced Night Repair (សេរ៉ូមលាបយប់)", "price": 75.00, "category": "serum", "barcode": "880600000007", "image": "estee_anr.jpg"},
    {"name": "Lancôme Advanced Génifique Serum", "price": 80.00, "category": "serum", "barcode": "880600000008", "image": "lancome_genifique.jpg"},
    {"name": "Olay Regenerist Micro-Sculpting Cream", "price": 25.00, "category": "moisturizer", "barcode": "880600000009", "image": "olay_regenerist.jpg"},
    {"name": "Neutrogena Hydro Boost Water Gel", "price": 16.00, "category": "moisturizer", "barcode": "880600000010", "image": "neutrogena_hydro.jpg"},
    {"name": "Kiehl's Ultra Facial Cream", "price": 32.00, "category": "moisturizer", "barcode": "880600000011", "image": "kiehls_cream.jpg"},
    {"name": "La Roche-Posay Anthelios UVmune 400 (ឡេការពារកម្តៅថ្ងៃ)", "price": 22.00, "category": "sunscreen", "barcode": "880600000012", "image": "laroche_sunscreen.jpg"},
    {"name": "Eucerin Sun Dry Touch (ឡេការពារកម្តៅថ្ងៃ)", "price": 24.00, "category": "sunscreen", "barcode": "880600000013", "image": "eucerin_sun.jpg"},
    {"name": "Mediheal Teatree Care Solution Mask (ម៉ាសបិទមុខ)", "price": 2.00, "category": "mask", "barcode": "880600000014", "image": "mediheal_teatree.jpg"},
    {"name": "Kiehl's Rare Earth Deep Pore Clay Mask (ម៉ាសភក់)", "price": 35.00, "category": "mask", "barcode": "880600000015", "image": "kiehls_claymask.jpg"},
    {"name": "Sulwhasoo Overnight Vitalizing Mask", "price": 50.00, "category": "mask", "barcode": "880600000016", "image": "sulwhasoo_mask.jpg"},
    {"name": "AHC Essential Real Eye Cream (គ្រីមលាបភ្នែក)", "price": 14.00, "category": "eye_cream", "barcode": "880600000017", "image": "ahc_eyecream.jpg"},
    {"name": "Clinique All About Eyes", "price": 34.00, "category": "eye_cream", "barcode": "880600000018", "image": "clinique_eyes.jpg"},
    {"name": "Laneige Lip Sleeping Mask (ម៉ាសលាបមាត់ចូលគេង)", "price": 18.00, "category": "mask", "barcode": "880600000019", "image": "laneige_lipmask.jpg"},
    {"name": "Vaseline Rosy Lips Therapy (ក្រែមការពារមាត់ប្រេះ)", "price": 3.50, "category": "lip_balm", "barcode": "880600000020", "image": "vaseline_lip.jpg"},

    # Additional Skincare
    {"name": "Nature Republic Aloe Vera 92% Soothing Gel (ជែលប្រទាលកន្ទុយក្រពើ)", "price": 5.00, "category": "moisturizer", "barcode": "880600000021", "image": "naturerepublic_aloe.jpg"},
    {"name": "Garnier Bright Complete Vitamin C Serum", "price": 12.00, "category": "serum", "barcode": "880600000022", "image": "garnier_vitc.jpg"},
    {"name": "L'Oréal Revitalift Hyaluronic Acid Serum", "price": 22.00, "category": "serum", "barcode": "880600000023", "image": "loreal_revitalift.jpg"},
    {"name": "SK-II Facial Treatment Clear Lotion", "price": 85.00, "category": "toner", "barcode": "880600000024", "image": "skii_lotion.jpg"},
    {"name": "AHA BHA PHA 30 Days Miracle Acne Clear Foam", "price": 13.00, "category": "cleanser", "barcode": "880600000025", "image": "somebymi_foam.jpg"},
    {"name": "Biore Makeup Remover Perfect Cleansing Water (ទឹកជូតមុខ)", "price": 9.00, "category": "remover", "barcode": "880600000026", "image": "biore_cleansingwater.jpg"},
    {"name": "Senka All Clear Water Micellar Formula", "price": 8.50, "category": "remover", "barcode": "880600000027", "image": "senka_allclear.jpg"},
    {"name": "Naturie Hatomugi Skin Conditioner", "price": 10.00, "category": "toner", "barcode": "880600000028", "image": "naturie_hatomugi.jpg"},
    {"name": "Melano CC Vitamin C Brightening Essence", "price": 14.00, "category": "serum", "barcode": "880600000029", "image": "melanocc_essence.jpg"},
    {"name": "Dr.Jart+ Cicapair Tiger Grass Color Correcting Treatment", "price": 42.00, "category": "moisturizer", "barcode": "880600000030", "image": "drjart_cicapair.jpg"},

    # --- MAKEUP ---
    {"name": "Estée Lauder Double Wear Foundation (ម្សៅទ្រនាប់)", "price": 45.00, "category": "foundation", "barcode": "880700000001", "image": "estee_doublewear.jpg"},
    {"name": "MAC Studio Fix Fluid Foundation", "price": 35.00, "category": "foundation", "barcode": "880700000002", "image": "mac_studiofix.jpg"},
    {"name": "NARS Radiant Creamy Concealer (ខុនស៊ីល័រ)", "price": 30.00, "category": "concealer", "barcode": "880700000003", "image": "nars_concealer.jpg"},
    {"name": "Tarte Shape Tape Concealer", "price": 29.00, "category": "concealer", "barcode": "880700000004", "image": "tarte_shapetape.jpg"},
    {"name": "Laura Mercier Translucent Loose Powder (ម្សៅហុយ)", "price": 39.00, "category": "powder", "barcode": "880700000005", "image": "laura_mercier_powder.jpg"},
    {"name": "Srichand Translucent Powder", "price": 8.00, "category": "powder", "barcode": "880700000006", "image": "srichand_powder.jpg"},
    {"name": "Urban Decay All Nighter Setting Spray (ទឹកបាញ់មុខ)", "price": 33.00, "category": "spray", "barcode": "880700000007", "image": "ud_settingspray.jpg"},
    {"name": "NARS Blush (Orgasm) (ម្សៅផាត់ថ្ពាល់)", "price": 32.00, "category": "blush", "barcode": "880700000008", "image": "nars_blush.jpg"},
    {"name": "Rare Beauty Soft Pinch Liquid Blush", "price": 23.00, "category": "blush", "barcode": "880700000009", "image": "rare_blush.jpg"},
    {"name": "Fenty Beauty Killawatt Highlighter (ហាយឡាយ)", "price": 36.00, "category": "highlighter", "barcode": "880700000010", "image": "fenty_highlighter.jpg"},
    {"name": "Benefit Hoola Bronzer", "price": 30.00, "category": "contour", "barcode": "880700000011", "image": "benefit_hoola.jpg"},
    {"name": "Anastasia Beverly Hills Dipbrow Pomade (សម្រាប់គូសចិញ្ចើម)", "price": 21.00, "category": "eyebrow", "barcode": "880700000012", "image": "abh_dipbrow.jpg"},
    {"name": "KATE Tokyo Designing Eyebrow 3D", "price": 12.00, "category": "eyebrow", "barcode": "880700000013", "image": "kate_eyebrow.jpg"},
    {"name": "Urban Decay Naked Heat Palette (ម្សៅផាត់ភ្នែក)", "price": 54.00, "category": "eyeshadow", "barcode": "880700000014", "image": "ud_naked.jpg"},
    {"name": "Kiss Me Heroine Make Eyeliner (អាយឡាញន័រ)", "price": 14.00, "category": "eyeliner", "barcode": "880700000015", "image": "heroine_eyeliner.jpg"},
    {"name": "L'Oréal Voluminous Lash Paradise (ម៉ាស្ការ៉ា)", "price": 11.00, "category": "mascara", "barcode": "880700000016", "image": "loreal_lashparadise.jpg"},
    {"name": "Dior Lip Glow Oil", "price": 38.00, "category": "lip_gloss", "barcode": "880700000017", "image": "dior_lipglow.jpg"},
    {"name": "YSL Rouge Pur Couture Lipstick (ក្រែមលាបមាត់)", "price": 39.00, "category": "lipstick", "barcode": "880700000018", "image": "ysl_rouge.jpg"},
    {"name": "Peripera Ink Velvet Tint (លីបទីន)", "price": 8.50, "category": "lip_tint", "barcode": "880700000019", "image": "peripera_ink.jpg"},
    {"name": "Rom&nd Juicy Lasting Tint", "price": 9.00, "category": "lip_tint", "barcode": "880700000020", "image": "romand_tint.jpg"},
    {"name": "Bobbi Brown Vitamin Enriched Face Base", "price": 64.00, "category": "primer", "barcode": "880700000021", "image": "bobbi_base.jpg"},
    {"name": "Benefit The POREfessional Primer (ប្រៃម័រ)", "price": 32.00, "category": "primer", "barcode": "880700000022", "image": "benefit_porefessional.jpg"},
    {"name": "Missha Perfect Cover BB Cream", "price": 16.00, "category": "foundation", "barcode": "880700000023", "image": "missha_bb.jpg"},
    {"name": "Laneige Neo Cushion Matte (ម្សៅទឹក Cushion)", "price": 28.00, "category": "foundation", "barcode": "880700000024", "image": "laneige_cushion.jpg"},
    {"name": "Charlotte Tilbury Airbrush Flawless Finish", "price": 45.00, "category": "powder", "barcode": "880700000025", "image": "ct_powder.jpg"},

    # Additional Makeup
    {"name": "Maybelline Superstay Matte Ink (ក្រែមលាបមាត់)", "price": 9.50, "category": "lipstick", "barcode": "880700000026", "image": "maybelline_superstay.jpg"},
    {"name": "Canmake Cream Cheek (ម្សៅថ្ពាល់)", "price": 10.00, "category": "blush", "barcode": "880700000027", "image": "canmake_cheek.jpg"},
    {"name": "Cathy Doll Speed White CC Cream", "price": 7.00, "category": "foundation", "barcode": "880700000028", "image": "cathydoll_cc.jpg"},
    {"name": "Mistine Super Illusion Eyebrow", "price": 4.50, "category": "eyebrow", "barcode": "880700000029", "image": "mistine_eyebrow.jpg"},
    {"name": "Innisfree No Sebum Mineral Powder (ម្សៅហុយ)", "price": 6.50, "category": "powder", "barcode": "880700000030", "image": "innisfree_nosebum.jpg"},

    # --- HAIR CARE ---
    {"name": "Head & Shoulders Cool Menthol (សាប៊ូកក់សក់)", "price": 5.50, "category": "shampoo", "barcode": "880800000001", "image": "head_shoulders.jpg"},
    {"name": "Clear Men Anti-Dandruff Shampoo", "price": 5.00, "category": "shampoo", "barcode": "880800000002", "image": "clear_men.jpg"},
    {"name": "Pantene 3 Minute Miracle Conditioner (ក្រែមបន្ទន់សក់)", "price": 5.50, "category": "conditioner", "barcode": "880800000003", "image": "pantene_3min.jpg"},
    {"name": "Olaplex No.4 Bond Maintenance Shampoo", "price": 30.00, "category": "shampoo", "barcode": "880800000004", "image": "olaplex_4.jpg"},
    {"name": "Olaplex No.5 Bond Maintenance Conditioner", "price": 30.00, "category": "conditioner", "barcode": "880800000005", "image": "olaplex_5.jpg"},
    {"name": "Mise En Scene Perfect Serum (សេរ៉ូមលាបសក់)", "price": 12.00, "category": "hair_oil", "barcode": "880800000006", "image": "mise_serum.jpg"},
    {"name": "Kerastase Elixir Ultime Hair Oil", "price": 50.00, "category": "hair_oil", "barcode": "880800000007", "image": "kerastase_oil.jpg"},
    {"name": "Liese Hello Bubble Hair Color (ថ្នាំលាបសក់)", "price": 10.50, "category": "hair_color", "barcode": "880800000008", "image": "liese_color.jpg"},
    {"name": "Garnier Color Naturals (ថ្នាំលាបសក់)", "price": 4.50, "category": "hair_color", "barcode": "880800000009", "image": "garnier_color.jpg"},
    {"name": "Batiste Dry Shampoo Original (សាប៊ូកក់សក់ស្ងួត)", "price": 8.00, "category": "shampoo", "barcode": "880800000010", "image": "batiste_dry.jpg"},
    {"name": "Shiseido Fino Premium Touch Hair Mask (ម៉ាសអប់សក់)", "price": 14.00, "category": "conditioner", "barcode": "880800000011", "image": "fino_mask.jpg"},
    {"name": "Tsubaki Premium Repair Hair Mask", "price": 15.00, "category": "conditioner", "barcode": "880800000012", "image": "tsubaki_mask.jpg"},
    {"name": "Briogeo Don't Despair, Repair! Mask", "price": 38.00, "category": "conditioner", "barcode": "880800000013", "image": "briogeo_mask.jpg"},
    {"name": "Aromatica Scalp Revival Scrub (ស្ក្រាប់ដុសស្បែកក្បាល)", "price": 22.00, "category": "conditioner", "barcode": "880800000014", "image": "aromatica_scrub.jpg"},
    {"name": "Lucido-L Argan Oil Hair Treatment", "price": 11.00, "category": "hair_oil", "barcode": "880800000015", "image": "lucido_oil.jpg"},

    # Additional Hair Care
    {"name": "Rejoice Rich Soft Smooth Shampoo", "price": 4.50, "category": "shampoo", "barcode": "880800000016", "image": "rejoice_shampoo.jpg"},
    {"name": "Dove Nutritive Solutions Intense Repair Shampoo", "price": 5.00, "category": "shampoo", "barcode": "880800000017", "image": "dove_intense.jpg"},
    {"name": "Sunsilk Perfect Straight Conditioner", "price": 4.00, "category": "conditioner", "barcode": "880800000018", "image": "sunsilk_straight.jpg"},
    {"name": "Diane Perfect Beauty Miracle You Hair Mask", "price": 13.00, "category": "conditioner", "barcode": "880800000019", "image": "diane_mask.jpg"},
    {"name": "Moremo Hair Essence Delightful Oil", "price": 16.00, "category": "hair_oil", "barcode": "880800000020", "image": "moremo_oil.jpg"},

    # --- BODY CARE ---
    {"name": "Vaseline UV Extra Brightening Lotion (ឡេលាបខ្លួនស)", "price": 6.50, "category": "body_lotion", "barcode": "880900000001", "image": "vaseline_uv.jpg"},
    {"name": "Jergens Ultra Healing Body Lotion", "price": 9.50, "category": "body_lotion", "barcode": "880900000002", "image": "jergens_healing.jpg"},
    {"name": "Cetaphil Moisturizing Lotion", "price": 18.00, "category": "body_lotion", "barcode": "880900000003", "image": "cetaphil_lotion.jpg"},
    {"name": "Bath & Body Works Japanese Cherry Blossom Wash (សាប៊ូដុសខ្លួន)", "price": 14.00, "category": "body_wash", "barcode": "880900000004", "image": "bbw_cherry.jpg"},
    {"name": "St. Ives Daily Hydrating Body Wash", "price": 7.00, "category": "body_wash", "barcode": "880900000005", "image": "stives_wash.jpg"},
    {"name": "Yoko Spa Milk Salt (អំបិលស្ក្រាប់ដុសខ្លួន)", "price": 2.50, "category": "body_scrub", "barcode": "880900000006", "image": "yoko_salt.jpg"},
    {"name": "Tree Hut Apricot Body Scrub", "price": 9.00, "category": "body_scrub", "barcode": "880900000007", "image": "treehut_scrub.jpg"},
    {"name": "Rexona Women Shower Clean Roll-On (រ៉ូលអនក្លៀក)", "price": 2.50, "category": "deodorant", "barcode": "880900000008", "image": "rexona_women.jpg"},
    {"name": "Dove Men+Care Antiperspirant", "price": 3.00, "category": "deodorant", "barcode": "880900000009", "image": "dove_men.jpg"},
    {"name": "L'Occitane Shea Butter Hand Cream (ឡេលាបដៃ)", "price": 29.00, "category": "tools", "barcode": "880900000010", "image": "loccitane_hand.jpg"},
    {"name": "Eucerin Advanced Repair Foot Cream (គ្រីមលាបកែងជើង)", "price": 12.00, "category": "tools", "barcode": "880900000011", "image": "eucerin_foot.jpg"},
    {"name": "Veet Hair Removal Cream (គ្រីមជម្រុះរោម)", "price": 6.50, "category": "body_wash", "barcode": "880900000012", "image": "veet_cream.jpg"},
    {"name": "Nivea Sun Protect & Moisture Body SPF50 (ឡេការពារកម្តៅថ្ងៃ)", "price": 12.50, "category": "sunscreen", "barcode": "880900000013", "image": "nivea_sun_body.jpg"},
    {"name": "Palmer's Cocoa Butter Formula Lotion", "price": 10.00, "category": "body_lotion", "barcode": "880900000014", "image": "palmers_cocoa.jpg"},
    {"name": "Johnson's Baby Bath (សាប៊ូដុសខ្លួនកូនក្មេង)", "price": 5.00, "category": "body_wash", "barcode": "880900000015", "image": "johnsons_baby.jpg"},
    {"name": "Lactacyd Feminine Wash (សាប៊ូអនាម័យ)", "price": 6.00, "category": "body_wash", "barcode": "880900000016", "image": "lactacyd.jpg"},
    {"name": "Bio-Oil Skincare Oil (ប្រេងលាបស្បែក)", "price": 14.50, "category": "body_lotion", "barcode": "880900000017", "image": "bio_oil.jpg"},
    {"name": "Olay Body Science Body Wash", "price": 7.50, "category": "body_wash", "barcode": "880900000018", "image": "olay_bodywash.jpg"},
    {"name": "Kojie San Skin Lightening Soap (សាប៊ូដុំធ្វើអោយស)", "price": 3.00, "category": "body_wash", "barcode": "880900000019", "image": "kojiesan.jpg"},
    {"name": "Burt's Bees Body Lotion", "price": 11.00, "category": "body_lotion", "barcode": "880900000020", "image": "burts_bees.jpg"},

    # Additional Body Care
    {"name": "A Bonne Spa Milk Salt (ស្ក្រាប់ដុសខ្លួន)", "price": 3.00, "category": "body_scrub", "barcode": "880900000021", "image": "abonne_salt.jpg"},
    {"name": "Mistine White Spa Body Lotion", "price": 5.50, "category": "body_lotion", "barcode": "880900000022", "image": "mistine_whitespa.jpg"},
    {"name": "Nivea Extra White Firming Body Serum", "price": 8.00, "category": "body_lotion", "barcode": "880900000023", "image": "nivea_serum.jpg"},
    {"name": "Dettol Feminine Wash (សាប៊ូអនាម័យ)", "price": 5.50, "category": "body_wash", "barcode": "880900000024", "image": "dettol_wash.jpg"},

    # --- FRAGRANCE, TOOLS, AND OTHERS ---
    {"name": "Chanel Coco Mademoiselle EDP (ទឹកអប់)", "price": 145.00, "category": "fragrance", "barcode": "881000000001", "image": "chanel_coco.jpg"},
    {"name": "Dior Sauvage EDT (ទឹកអប់បុរស)", "price": 110.00, "category": "fragrance", "barcode": "881000000002", "image": "dior_sauvage.jpg"},
    {"name": "YSL Libre Eau de Parfum", "price": 130.00, "category": "fragrance", "barcode": "881000000003", "image": "ysl_libre.jpg"},
    {"name": "Jo Malone English Pear & Freesia", "price": 140.00, "category": "fragrance", "barcode": "881000000004", "image": "jomalone_pear.jpg"},
    {"name": "Versace Bright Crystal", "price": 85.00, "category": "fragrance", "barcode": "881000000005", "image": "versace_crystal.jpg"},
    {"name": "Calvin Klein CK One", "price": 65.00, "category": "fragrance", "barcode": "881000000006", "image": "ck_one.jpg"},
    {"name": "Real Techniques Miracle Complexion Sponge (អេប៉ុងផាត់មុខ)", "price": 8.00, "category": "tools", "barcode": "881000000007", "image": "rt_sponge.jpg"},
    {"name": "Shiseido Eyelash Curler (ប្រដាប់គាបរោមភ្នែក)", "price": 22.00, "category": "tools", "barcode": "881000000008", "image": "shiseido_curler.jpg"},
    {"name": "Sigma Everyday Face Brush Set (ឈុតជក់ផាត់មុខ)", "price": 45.00, "category": "tools", "barcode": "881000000009", "image": "sigma_brushes.jpg"},
    {"name": "Garnier Micellar Cleansing Water (Pink)", "price": 8.50, "category": "remover", "barcode": "881000000010", "image": "garnier_micellar.jpg"},
    {"name": "Banila Co Clean It Zero Cleansing Balm", "price": 18.00, "category": "remover", "barcode": "881000000011", "image": "banila_zero.jpg"},
    {"name": "Bifesta Eye Makeup Remover", "price": 9.00, "category": "remover", "barcode": "881000000012", "image": "bifesta_remover.jpg"},
    {"name": "DHC Collagen Supplements (60 Days)", "price": 15.00, "category": "supplement", "barcode": "881000000013", "image": "dhc_collagen.jpg"},
    {"name": "Vistra Marine Collagen TriPeptide", "price": 20.00, "category": "supplement", "barcode": "881000000014", "image": "vistra_collagen.jpg"},
    {"name": "Blackmores Vitamin C 1000mg", "price": 25.00, "category": "supplement", "barcode": "881000000015", "image": "blackmores_vitc.jpg"},
    {"name": "Swisse Grape Seed Extract", "price": 28.00, "category": "supplement", "barcode": "881000000016", "image": "swisse_grape.jpg"},
    {"name": "Innisfree No Sebum Mineral Powder", "price": 6.50, "category": "powder", "barcode": "881000000017", "image": "innisfree_powder.jpg"},
    {"name": "NSP Essential Beauty Gift Set", "price": 45.00, "category": "gift_set", "barcode": "881000000018", "image": "nsp_giftset.jpg"},
    {"name": "SK-II Facial Treatment Essence", "price": 180.00, "category": "serum", "barcode": "881000000019", "image": "skii_essence.jpg"},
    {"name": "La Mer Crème de la Mer", "price": 195.00, "category": "moisturizer", "barcode": "881000000020", "image": "lamer_creme.jpg"},
    
    # Additional Fragrance & Tools
    {"name": "Victoria's Secret Pure Seduction Body Mist (ទឹកបាញ់ខ្លួន)", "price": 18.00, "category": "fragrance", "barcode": "881000000021", "image": "vs_pureseduction.jpg"},
    {"name": "Bath & Body Works Warm Vanilla Sugar", "price": 15.00, "category": "fragrance", "barcode": "881000000022", "image": "bbw_vanilla.jpg"},
    {"name": "Daiso Deep Cleansing Oil", "price": 12.00, "category": "remover", "barcode": "881000000023", "image": "daiso_oil.jpg"},
    {"name": "Muji Cotton Pads (សំឡីជូតមុខ)", "price": 3.00, "category": "tools", "barcode": "881000000024", "image": "muji_cotton.jpg"},
    {"name": "Laneige Lip Sleeping Mask (Mini)", "price": 6.00, "category": "mask", "barcode": "881000000025", "image": "laneige_lip_mini.jpg"},
    {"name": "NSP Signature Makeup Brush Set", "price": 35.00, "category": "tools", "barcode": "881000000026", "image": "nsp_brushset.jpg"}
]
# Combine both lists together
all_products = cosmetic_products

print("Starting upload to Firebase...")
items_ref = db.collection('items')

for product in all_products:
    doc_id = product["barcode"]
    items_ref.document(doc_id).set({
        "name": product["name"],
        "price": product["price"],
        "category": product["category"],
        "barcode": product["barcode"],
        "image": product["image"]
    })
    print(f"Uploaded: {product['name']}")

print(f"✅ Success! All {len(all_products)} products have been added to your POS Database.")