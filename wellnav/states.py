"""US states and Texas RRC county partitions (3-digit API prefix)."""

from __future__ import annotations

US_STATES = {
    "al": "Alabama",
    "ak": "Alaska",
    "az": "Arizona",
    "ar": "Arkansas",
    "ca": "California",
    "co": "Colorado",
    "ct": "Connecticut",
    "de": "Delaware",
    "fl": "Florida",
    "ga": "Georgia",
    "hi": "Hawaii",
    "id": "Idaho",
    "il": "Illinois",
    "in": "Indiana",
    "ia": "Iowa",
    "ks": "Kansas",
    "ky": "Kentucky",
    "la": "Louisiana",
    "me": "Maine",
    "md": "Maryland",
    "ma": "Massachusetts",
    "mi": "Michigan",
    "mn": "Minnesota",
    "ms": "Mississippi",
    "mo": "Missouri",
    "mt": "Montana",
    "ne": "Nebraska",
    "nv": "Nevada",
    "nh": "New Hampshire",
    "nj": "New Jersey",
    "nm": "New Mexico",
    "ny": "New York",
    "nc": "North Carolina",
    "nd": "North Dakota",
    "oh": "Ohio",
    "ok": "Oklahoma",
    "or": "Oregon",
    "pa": "Pennsylvania",
    "ri": "Rhode Island",
    "sc": "South Carolina",
    "sd": "South Dakota",
    "tn": "Tennessee",
    "tx": "Texas",
    "ut": "Utah",
    "vt": "Vermont",
    "va": "Virginia",
    "wa": "Washington",
    "wv": "West Virginia",
    "wi": "Wisconsin",
    "wy": "Wyoming",
}

# RRC API county prefix == Texas county FIPS (3 digits).
TX_COUNTIES: list[tuple[str, str]] = [
    ("001", "ANDERSON"), ("003", "ANDREWS"), ("005", "ANGELINA"), ("007", "ARANSAS"),
    ("009", "ARCHER"), ("011", "ARMSTRONG"), ("013", "ATASCOSA"), ("015", "AUSTIN"),
    ("017", "BAILEY"), ("019", "BANDERA"), ("021", "BASTROP"), ("023", "BAYLOR"),
    ("025", "BEE"), ("027", "BELL"), ("029", "BEXAR"), ("031", "BLANCO"),
    ("033", "BORDEN"), ("035", "BOSQUE"), ("037", "BOWIE"), ("039", "BRAZORIA"),
    ("041", "BRAZOS"), ("043", "BREWSTER"), ("045", "BRISCOE"), ("047", "BROOKS"),
    ("049", "BROWN"), ("051", "BURLESON"), ("053", "BURNET"), ("055", "CALDWELL"),
    ("057", "CALHOUN"), ("059", "CALLAHAN"), ("061", "CAMERON"), ("063", "CAMP"),
    ("065", "CARSON"), ("067", "CASS"), ("069", "CASTRO"), ("071", "CHAMBERS"),
    ("073", "CHEROKEE"), ("075", "CHILDRESS"), ("077", "CLAY"), ("079", "COCHRAN"),
    ("081", "COKE"), ("083", "COLEMAN"), ("085", "COLLIN"), ("087", "COLLINGSWORTH"),
    ("089", "COLORADO"), ("091", "COMAL"), ("093", "COMANCHE"), ("095", "CONCHO"),
    ("097", "COOKE"), ("099", "CORYELL"), ("101", "COTTLE"), ("103", "CRANE"),
    ("105", "CROCKETT"), ("107", "CROSBY"), ("109", "CULBERSON"), ("111", "DALLAM"),
    ("113", "DALLAS"), ("115", "DAWSON"), ("117", "DEAF SMITH"), ("119", "DELTA"),
    ("121", "DENTON"), ("123", "DE WITT"), ("125", "DICKENS"), ("127", "DIMMIT"),
    ("129", "DONLEY"), ("131", "DUVAL"), ("133", "EASTLAND"), ("135", "ECTOR"),
    ("137", "EDWARDS"), ("139", "ELLIS"), ("141", "EL PASO"), ("143", "ERATH"),
    ("145", "FALLS"), ("147", "FANNIN"), ("149", "FAYETTE"), ("151", "FISHER"),
    ("153", "FLOYD"), ("155", "FOARD"), ("157", "FORT BEND"), ("159", "FRANKLIN"),
    ("161", "FREESTONE"), ("163", "FRIO"), ("165", "GAINES"), ("167", "GALVESTON"),
    ("169", "GARZA"), ("171", "GILLESPIE"), ("173", "GLASSCOCK"), ("175", "GOLIAD"),
    ("177", "GONZALES"), ("179", "GRAY"), ("181", "GRAYSON"), ("183", "GREGG"),
    ("185", "GRIMES"), ("187", "GUADALUPE"), ("189", "HALE"), ("191", "HALL"),
    ("193", "HAMILTON"), ("195", "HANSFORD"), ("197", "HARDEMAN"), ("199", "HARDIN"),
    ("201", "HARRIS"), ("203", "HARRISON"), ("205", "HARTLEY"), ("207", "HASKELL"),
    ("209", "HAYS"), ("211", "HEMPHILL"), ("213", "HENDERSON"), ("215", "HIDALGO"),
    ("217", "HILL"), ("219", "HOCKLEY"), ("221", "HOOD"), ("223", "HOPKINS"),
    ("225", "HOUSTON"), ("227", "HOWARD"), ("229", "HUDSPETH"), ("231", "HUNT"),
    ("233", "HUTCHINSON"), ("235", "IRION"), ("237", "JACK"), ("239", "JACKSON"),
    ("241", "JASPER"), ("243", "JEFF DAVIS"), ("245", "JEFFERSON"), ("247", "JIM HOGG"),
    ("249", "JIM WELLS"), ("251", "JOHNSON"), ("253", "JONES"), ("255", "KARNES"),
    ("257", "KAUFMAN"), ("259", "KENDALL"), ("261", "KENEDY"), ("263", "KENT"),
    ("265", "KERR"), ("267", "KIMBLE"), ("269", "KING"), ("271", "KINNEY"),
    ("273", "KLEBERG"), ("275", "KNOX"), ("277", "LAMAR"), ("279", "LAMB"),
    ("281", "LAMPASAS"), ("283", "LA SALLE"), ("285", "LAVACA"), ("287", "LEE"),
    ("289", "LEON"), ("291", "LIBERTY"), ("293", "LIMESTONE"), ("295", "LIPSCOMB"),
    ("297", "LIVE OAK"), ("299", "LLANO"), ("301", "LOVING"), ("303", "LUBBOCK"),
    ("305", "LYNN"), ("307", "MCCULLOCH"), ("309", "MCLENNAN"), ("311", "MCMULLEN"),
    ("313", "MADISON"), ("315", "MARION"), ("317", "MARTIN"), ("319", "MASON"),
    ("321", "MATAGORDA"), ("323", "MAVERICK"), ("325", "MEDINA"), ("327", "MENARD"),
    ("329", "MIDLAND"), ("331", "MILAM"), ("333", "MILLS"), ("335", "MITCHELL"),
    ("337", "MONTAGUE"), ("339", "MONTGOMERY"), ("341", "MOORE"), ("343", "MORRIS"),
    ("345", "MOTLEY"), ("347", "NACOGDOCHES"), ("349", "NAVARRO"), ("351", "NEWTON"),
    ("353", "NOLAN"), ("355", "NUECES"), ("357", "OCHILTREE"), ("359", "OLDHAM"),
    ("361", "ORANGE"), ("363", "PALO PINTO"), ("365", "PANOLA"), ("367", "PARKER"),
    ("369", "PARMER"), ("371", "PECOS"), ("373", "POLK"), ("375", "POTTER"),
    ("377", "PRESIDIO"), ("379", "RAINS"), ("381", "RANDALL"), ("383", "REAGAN"),
    ("385", "REAL"), ("387", "RED RIVER"), ("389", "REEVES"), ("391", "REFUGIO"),
    ("393", "ROBERTS"), ("395", "ROBERTSON"), ("397", "ROCKWALL"), ("399", "RUNNELS"),
    ("401", "RUSK"), ("403", "SABINE"), ("405", "SAN AUGUSTINE"), ("407", "SAN JACINTO"),
    ("409", "SAN PATRICIO"), ("411", "SAN SABA"), ("413", "SCHLEICHER"), ("415", "SCURRY"),
    ("417", "SHACKELFORD"), ("419", "SHELBY"), ("421", "SHERMAN"), ("423", "SMITH"),
    ("425", "SOMERVELL"), ("427", "STARR"), ("429", "STEPHENS"), ("431", "STERLING"),
    ("433", "STONEWALL"), ("435", "SUTTON"), ("437", "SWISHER"), ("439", "TARRANT"),
    ("441", "TAYLOR"), ("443", "TERRELL"), ("445", "TERRY"), ("447", "THROCKMORTON"),
    ("449", "TITUS"), ("451", "TOM GREEN"), ("453", "TRAVIS"), ("455", "TRINITY"),
    ("457", "TYLER"), ("459", "UPSHUR"), ("461", "UPTON"), ("463", "UVALDE"),
    ("465", "VAL VERDE"), ("467", "VAN ZANDT"), ("469", "VICTORIA"), ("471", "WALKER"),
    ("473", "WALLER"), ("475", "WARD"), ("477", "WASHINGTON"), ("479", "WEBB"),
    ("481", "WHARTON"), ("483", "WHEELER"), ("485", "WICHITA"), ("487", "WILBARGER"),
    ("489", "WILLACY"), ("491", "WILLIAMSON"), ("493", "WILSON"), ("495", "WINKLER"),
    ("497", "WISE"), ("499", "WOOD"), ("501", "YOAKUM"), ("503", "YOUNG"),
    ("505", "ZAPATA"), ("507", "ZAVALA"),
]

TX_COUNTY_NAME = {code: name for code, name in TX_COUNTIES}

# GIS SYMNUM values that are still permits, not as-drilled wells.
PERMIT_SYMNUMS = {2, 9}  # Permitted Location, Canceled Location
DEFAULT_PERMIT_LIFETIME_DAYS = 730


def normalize_state(code: str) -> str:
    key = (code or "").strip().lower()
    if key not in US_STATES:
        raise ValueError(f"Unknown state code: {code}")
    return key


def wells_table(state: str) -> str:
    return f"wells_{normalize_state(state)}"


def permits_table(state: str) -> str:
    return f"permits_{normalize_state(state)}"
