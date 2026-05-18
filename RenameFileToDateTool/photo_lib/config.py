"""Central config for the photo library tooling.

Everything that's user- or machine-specific lives here so the other modules can
stay portable. To use this on a different machine or with a different default
timezone, edit the values below.
"""

# Default timezone for any photo whose own metadata doesn't carry a TZ offset.
# IANA name (resolvable by both ``zoneinfo`` and ``dateutil.tz``).
LOCAL_TIMEZONE_NAME = "Pacific/Auckland"

# Master library root — top-level folder containing the year subfolders.
MASTER_ROOT = r"D:\Files\Pictures and Videos"

# Inbox folder name (under MASTER_ROOT) where new batches land before being
# ingested into the master year folders.
INBOX_FOLDER_NAME = "_Inbox"

# Pre-2010 photos are bundled into one folder rather than per-year subfolders.
BUNDLED_EARLY_FOLDER = "2000 - 2010"
BUNDLED_EARLY_YEAR_RANGE = (2000, 2011)  # range() args — last year is exclusive

# How many sample files per (year, extension) the audit script inspects.
AUDIT_SAMPLES_PER_TYPE = 5
