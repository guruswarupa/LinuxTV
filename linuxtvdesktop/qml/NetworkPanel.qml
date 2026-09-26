import QtQuick 2.15

Column {
    id: root
    spacing: 12
    property var selectedNetwork: null

    Text {
        width: parent.width
        text: "Scan for nearby networks, enter a password if needed, and connect."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Rectangle {
        width: parent.width
        height: Math.min(220, Math.max(60, listView.contentHeight))
        radius: 8
        color: theme.surface_alt
        clip: true

        ListView {
            id: listView
            anchors.fill: parent
            model: backend.panelData.networks || []
            delegate: Rectangle {
                width: listView.width
                height: 44
                color: (root.selectedNetwork && root.selectedNetwork.ssid === modelData.ssid) ? theme.accent : "transparent"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    elide: Text.ElideRight
                    text: modelData.label || modelData.ssid
                    color: theme.text
                    font.family: theme.font_family
                    font.pixelSize: 13
                }
                MouseArea { anchors.fill: parent; onClicked: root.selectedNetwork = modelData }
            }
        }
    }

    Text {
        visible: (backend.panelData.networks || []).length === 0
        text: backend.panelBusy ? "Scanning..." : "No networks yet. Tap Refresh."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
    }

    Rectangle {
        width: parent.width
        height: 40
        radius: 8
        color: theme.surface_alt
        border.width: pwInput.activeFocus ? 2 : 0
        border.color: theme.accent

        TextInput {
            id: pwInput
            anchors.fill: parent
            anchors.margins: 10
            verticalAlignment: TextInput.AlignVCenter
            color: theme.text
            font.family: theme.font_family
            font.pixelSize: 14
            echoMode: TextInput.Password
            selectByMouse: true
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 10
            text: "Wi-Fi password"
            color: theme.text_muted
            font.family: theme.font_family
            font.pixelSize: 14
            visible: pwInput.text.length === 0
        }
    }

    Row {
        spacing: 10

        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.surface_alt
            opacity: backend.panelBusy ? 0.5 : 1
            Text { anchors.centerIn: parent; text: "Refresh"; color: theme.text; font.family: theme.font_family; font.pixelSize: 13 }
            MouseArea { anchors.fill: parent; enabled: !backend.panelBusy; onClicked: backend.panelAction("refresh", {}) }
        }
        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.surface_alt
            opacity: (!backend.panelBusy && root.selectedNetwork) ? 1 : 0.5
            Text { anchors.centerIn: parent; text: "Forget"; color: theme.text; font.family: theme.font_family; font.pixelSize: 13 }
            MouseArea {
                anchors.fill: parent
                enabled: !backend.panelBusy && root.selectedNetwork !== null
                onClicked: backend.panelAction("forget", { ssid: root.selectedNetwork.ssid })
            }
        }
        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.accent
            opacity: (!backend.panelBusy && root.selectedNetwork) ? 1 : 0.5
            Text { anchors.centerIn: parent; text: "Connect"; color: "white"; font.family: theme.font_family; font.pixelSize: 13; font.bold: true }
            MouseArea {
                anchors.fill: parent
                enabled: !backend.panelBusy && root.selectedNetwork !== null
                onClicked: backend.panelAction("connect", {
                    ssid: root.selectedNetwork.ssid,
                    security: root.selectedNetwork.security || "",
                    password: pwInput.text
                })
            }
        }
    }
}
