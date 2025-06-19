    #!/usr/bin/env bash
    # fetch_avail_and_merge.sh
    #
    # 1. Reads Facility IDs from download.csv (header + rows)
    # 2. Fetches one month of availability for each campground
    # 3. Rate‑limits to stay under Recreation.gov thresholds
    # 4. Produces a single JSON object mapping FacilityID → availability payload
    #
    # Prereqs:
    #   - curl, tail, cut, sleep, cat (standard on macOS/Linux)
    #   - jq (for the final merge)  → brew install jq
    #
    # Usage:
    #   bash fetch_avail_and_merge.sh 2025-08
    #
    #   Creates:
    #       avail_<ID>.json  (one per campground)
    #       all_avail_<MONTH>.json  (merged)
    #

    set -euo pipefail

    if [ $# -ne 1 ]; then
      echo "Usage: $0 YYYY-MM"
      exit 1
    fi

    MONTH="$1"                       # e.g. 2025-08
    START_DATE="${MONTH}-01T00%3A00%3A00.000Z"
    OUT_MERGED="all_avail_${MONTH}.json"

    echo "Reading IDs from download.csv..."
    IDS=$(tail -n +2 download.csv | cut -d',' -f1)
    TOTAL=$(echo "$IDS" | wc -l | tr -d ' ')

    echo "Found $TOTAL Facility IDs"

    # Fetch loop (single process; ~1.5 req/s with 0.6 s sleep)
    i=0
    for id in $IDS; do
      i=$((i+1))
      OUT_FILE="avail_${id}.json"
      TMP_OUT_FILE="${OUT_FILE}.tmp" # Temporary file for download

      printf '[%4d/%4d] ID %s ... ' "$i" "$TOTAL" "$id"

      # Check if the final output file already exists and is non-empty
      if [ -s "$OUT_FILE" ]; then
        echo "skipped (already exists)"
        continue # Move to the next ID
      fi

      # Attempt to download to the temporary file
      # Added -L to follow redirects
      if curl -f -s -L \
        "https://www.recreation.gov/api/camps/availability/campground/${id}/month?start_date=${START_DATE}" \
        -H "Accept: application/json" \
        -o "$TMP_OUT_FILE"; then
        # On successful download, move the temporary file to the final filename
        mv "$TMP_OUT_FILE" "$OUT_FILE"
        echo "done"
      else
        # If curl fails (e.g., HTTP error, network issue)
        ret_code=$?
        echo "failed (curl error code $ret_code)"
        rm -f "$TMP_OUT_FILE" # Clean up partial temporary file
        exit $ret_code # Exit if a download fails
      fi
      sleep 0.6          # throttle (adjust if you like)
    done

    echo "" # Add a newline for better output separation
    echo "Merging into ${OUT_MERGED} ..."
    TMP_MERGED="${OUT_MERGED}.tmpmerge" # Temporary file for merging

    # Count available .json files to merge using find for robustness
    # Suppress errors from find in case of permission issues, though unlikely for maxdepth 1
    num_avail_files=$(find . -maxdepth 1 -name 'avail_*.json' -type f -print0 2>/dev/null | tr -cd '\0' | wc -c)

    if [ "$num_avail_files" -eq 0 ]; then
      echo "No avail_*.json files found to merge. Creating an empty JSON object: $OUT_MERGED"
      printf '{}\n' > "$OUT_MERGED"
    else
      echo "Found $num_avail_files 'avail_*.json' file(s) to merge."
      # Build a single JSON object into the temporary merge file
      printf '{' > "$TMP_MERGED"
      first=true
      # Use find to get the list of files to avoid issues with too many files for globbing
      # and to ensure we only process actual files. Using null delimiter for safety.
      find . -maxdepth 1 -name 'avail_*.json' -type f -print0 | while IFS= read -r -d $'\0' f; do
        # Ensure file is non-empty before processing
        if [ ! -s "$f" ]; then 
            echo "Warning: Skipping empty file during merge: $(basename "$f")"
            continue
        fi

        # Extract ID from filename (e.g., avail_123.json -> 123)
        id_from_filename=$(basename "$f" .json)
        id_from_filename=${id_from_filename#avail_}

        if [ "$first" = true ]; then
          first=false
        else
          printf ',' >> "$TMP_MERGED"
        fi
        printf '"%s":' "$id_from_filename" >> "$TMP_MERGED"
        cat "$f" >> "$TMP_MERGED"
      done
      
      # Finalize JSON structure
      if [ "$first" = true ]; then # If no files were actually appended (e.g., all were empty)
          printf '{}\n' > "$TMP_MERGED" # Ensure it's a valid empty JSON object
      else
          printf '}\n' >> "$TMP_MERGED" # Close JSON object and add a newline
      fi

      # Atomically replace the old merged file with the new one
      mv "$TMP_MERGED" "$OUT_MERGED"
      echo "Merged availability written to $OUT_MERGED"
    fi

    echo "Example query:"
    echo "  jq '.["232450"].campsites | keys[0]' $OUT_MERGED"
