SonarX 6.3.3 - English Installer

This package contains the English-only installer and its regression tests.

To publish the English installer:
1. Replace the install.sh file in the ROOT of the GitHub repository.
2. Keep the tests/ directory structure if you also want to run the regression tests.
3. Commit and push the change to the main branch.
4. Verify the published file before running it:
   curl -fsSL https://raw.githubusercontent.com/Amirtn9/sonarx/main/install.sh | grep -nE 'Select an option|Install / Repair|FULL WIPE'
5. Then run:
   bash <(curl -fsSL https://raw.githubusercontent.com/Amirtn9/sonarx/main/install.sh) install

Important: The curl command downloads the file from GitHub. Replacing a local file
or extracting this ZIP does not change the GitHub repository. The new file must be
committed and pushed before the URL serves the English version.
