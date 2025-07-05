from apify_client import ApifyClient
from dotenv import load_dotenv
import os
import json

# Load environment variables
load_dotenv()

# Get API token
api_token = os.getenv("APIFY_API_TOKEN")

# Initialize client once (global)
client = ApifyClient(api_token)


def scrape_linkedin_profile(profile_url: str) -> dict:
    """
    📄 Scrapes a LinkedIn profile using Apify and returns the data as a Python dict.
    """
    try:
        run_input = {"profileUrls": [profile_url]}
        run = client.actor("dev_fusion/Linkedin-Profile-Scraper").call(run_input=run_input)

        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

        if items:
            with open("scraped_profile.json", "w") as f:
                json.dump(items[0], f, indent=2)
            return items[0]
        else:
            print("⚠️ No data found in dataset.")
            return {}
    except Exception as e:
        print(f"❌ Error during scraping: {e}")
        return {}


# 🧪 OPTIONAL: test code only runs when this file is executed directly
if __name__ == "__main__":
    test_url = "https://www.linkedin.com/in/sri-vallabh-tammireddy/"
    profile = scrape_linkedin_profile(test_url)
    print(json.dumps(profile, indent=2))
