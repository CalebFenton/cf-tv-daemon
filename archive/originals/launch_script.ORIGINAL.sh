#!/bin/sh
package_name="pisa-service"

package_info==$(pkgcmd -l | grep $package_name)
#package_info="system apps     pkg_type [tpk]  pkgid [$package_name]     name [pisa-service]     version [22.27.124]     storage [internal]"

package_id=$(echo $package_info | cut -d'[' -f3 | cut -d']' -f1 | cut -d\. -f1-4 | tr -d ' ')
package_version=$(echo $package_info | cut -d'[' -f5 | cut -d']' -f1 | cut -d\. -f1 | tr -d ' ')

if [ $package_version -ge 30 ]; then
	echo "Package ID($package_id) / Version($package_version) - Starting new service through systemd"

	exec /opt/usr/apps/com.samsung.tv.pisa-service/bin/adagent-service
else
	echo "Package ID($package_id) / Version($package_version) - Starting legacy service through $package_name"
fi

##UEP630x2b2bc6e0d30564d8dffedecc7fb0c8a6c8ac9af6a60f4dd2cdfd5caf0ef815b446711ae4ba723e6f4563576da0996398e275cc2485abe36790d9168fbc6704d1569a3915135f8cabb347c345b3b1a163b93937d74087fe140ce0a3db761f204a76e8b45a3143ac8995ec7b9155e1beb0d6b7bcfe94d95ab341cfcbf7cb9fb180d6a9e65873cec8b4b6d80dcaac51d108bd25bd08bc7ec348edc5bb0550860df5f71faa93a0202506a65c3f1e73e079cea9ae61084b62e9b3de30fe4630ceeb1b4b5aeb40fb618a0f1490bd0097abfa40acf612fe54cfc419361d36b4a48681de44c60fb9ecbe60e198aa955c627da1385a9dac7ed6453c15537942340dd264e4AAACAg==:UEP